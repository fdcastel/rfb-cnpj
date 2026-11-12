"""Download and extract the RFB open CNPJ data (layers 0 and 1).

Layer 2 (parquet) and 3 (views) are produced by dbt (see ./dbt).
"""

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote
from zipfile import ZipFile

import httpx
from tqdm import tqdm

#
# Constants
#

# Since 2025 the RFB files are served by a Nextcloud public share ("SERPRO+").
#   Browse: https://arquivos.receitafederal.gov.br/index.php/s/gn672Ad4CF8N6TK
#   Files are listed (PROPFIND) and downloaded (GET) through its public WebDAV endpoint.
SHARE_TOKEN = "gn672Ad4CF8N6TK"
DAV_ROOT = f"https://arquivos.receitafederal.gov.br/public.php/dav/files/{SHARE_TOKEN}/"

CNPJ_FOLDER = "Dados/Cadastros/CNPJ/"
REGIME_TRIBUTARIO_FOLDER = "Dados/Obrigacoes_Acessorias/ECF/"

# Files not used by the dbt models.
CNPJ_EXCLUDE = re.compile(r"^Socios\d*\.zip$", re.IGNORECASE)
REGIME_TRIBUTARIO_INCLUDE = re.compile(r"^entidades-.*\.zip$", re.IGNORECASE)

MAX_CONCURRENT_DOWNLOADS = 4
DOWNLOAD_RETRIES = 10

DEFAULT_DATA_DIR = Path("./data")
DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"
DATA_DIR_ENV = "RFB_CNPJ_DATA_DIR"
ZIP_SOURCES = "0-zip_sources"
CSV_SOURCES = "1-csv_sources"
PARQUET_SOURCES = "2-parquet_sources"
SNAPSHOT_MARKER = ".snapshot"
CSV_FORMAT = "utf-8"  # bump to force re-extraction when the csv layer format changes

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


@dataclass(frozen=True)
class RemoteEntry:
    path: str  # relative to DAV_ROOT, unquoted, folders end with '/'
    size: int

    @property
    def name(self) -> str:
        return self.path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def is_folder(self) -> bool:
        return self.path.endswith("/")


#
# Remote listing (WebDAV)
#

_RESPONSE_RE = re.compile(r"<d:response>(.*?)</d:response>", re.S)
_HREF_RE = re.compile(r"<d:href>(.*?)</d:href>", re.S)
_SIZE_RE = re.compile(r"<d:getcontentlength>(\d+)</d:getcontentlength>")


def parse_propfind(xml: str, folder: str) -> list[RemoteEntry]:
    """Parse a Nextcloud PROPFIND (Depth: 1) response. Returns the children of `folder`."""
    prefix = f"/public.php/dav/files/{SHARE_TOKEN}/"
    entries = []
    for response in _RESPONSE_RE.findall(xml):
        href = unquote(_HREF_RE.search(response).group(1))
        path = href.split(prefix, 1)[1] if prefix in href else href
        if path.rstrip("/") == folder.rstrip("/"):
            continue  # the folder itself
        size = _SIZE_RE.search(response)
        entries.append(RemoteEntry(path, int(size.group(1)) if size else 0))
    return entries


def list_remote(client: httpx.Client, folder: str) -> list[RemoteEntry]:
    response = client.request(
        "PROPFIND", DAV_ROOT + quote(folder), headers={"Depth": "1"}
    )
    response.raise_for_status()
    return parse_propfind(response.text, folder)


def available_months(client: httpx.Client) -> list[str]:
    return sorted(
        e.name
        for e in list_remote(client, CNPJ_FOLDER)
        if e.is_folder and MONTH_RE.match(e.name)
    )


def files_to_download(client: httpx.Client, month: str) -> list[RemoteEntry]:
    cnpj = [
        e
        for e in list_remote(client, f"{CNPJ_FOLDER}{month}/")
        if not e.is_folder and e.name.lower().endswith(".zip") and not CNPJ_EXCLUDE.match(e.name)
    ]
    regime = [
        e
        for e in list_remote(client, REGIME_TRIBUTARIO_FOLDER)
        if not e.is_folder and REGIME_TRIBUTARIO_INCLUDE.match(e.name)
    ]
    if not cnpj:
        raise RuntimeError(f"No CNPJ files found for month {month}.")
    if not regime:
        raise RuntimeError("No 'regime tributario' files found.")
    return cnpj + regime


#
# Download
#


async def download_file(
    client: httpx.AsyncClient, entry: RemoteEntry, target: Path, progress: tqdm
) -> None:
    """Download `entry` to `target`. Resumes a previous partial download (`.part`) with HTTP Range."""
    if target.is_file() and target.stat().st_size == entry.size:
        progress.update(entry.size)
        return

    part = target.with_name(target.name + ".part")
    done = part.stat().st_size if part.is_file() else 0
    if done > entry.size:
        part.unlink()
        done = 0
    progress.update(done)

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            headers = {"Range": f"bytes={done}-"} if done else {}
            async with client.stream("GET", DAV_ROOT + quote(entry.path), headers=headers) as response:
                response.raise_for_status()
                if done and response.status_code != 206:
                    # Server ignored the Range header: start over.
                    progress.update(-done)
                    done = 0
                with open(part, "ab" if done else "wb") as f:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        progress.update(len(chunk))
            if done != entry.size:
                raise IOError(f"expected {entry.size} bytes, got {done}")
            part.replace(target)
            return
        except (httpx.HTTPError, IOError) as e:
            if attempt == DOWNLOAD_RETRIES:
                raise RuntimeError(f"{entry.name}: download failed after {attempt} attempts") from e
            tqdm.write(f"  {entry.name}: {e!r} -- resuming at {done} bytes ({attempt}/{DOWNLOAD_RETRIES})")
            await asyncio.sleep(min(5 * attempt, 30))


async def download_files(entries: list[tuple[RemoteEntry, Path]]) -> None:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    total = sum(e.size for e, _ in entries)
    # A short read timeout detects stalled transfers (seen on this server); they resume.
    timeout = httpx.Timeout(60.0, read=60.0)

    with tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024, desc="  download") as progress:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

            async def bounded(entry, target):
                async with semaphore:
                    await download_file(client, entry, target, progress)

            await asyncio.gather(*(bounded(e, t) for e, t in entries))


#
# Extraction
#


def extract_zip(zip_file: Path, output_path: Path) -> list[Path]:
    """Extract every member of `zip_file` into `output_path` (flat), converting latin-1 to utf-8.

    The conversion is lossless (latin-1 maps every byte). It is needed because a few RFB
    rows carry stray C1 control bytes (0x80-0x9F) that DuckDB's latin-1 reader rejects.
    Files are written to `.part` and renamed, so an existing target is always complete.
    """
    extracted = []
    with ZipFile(zip_file) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            target = output_path / Path(member.filename).name
            if not target.is_file():
                part = target.with_name(target.name + ".part")
                with z.open(member) as s, open(part, "wb") as t:
                    while chunk := s.read(4 * 1024 * 1024):
                        t.write(chunk.decode("latin-1").encode("utf-8"))
                part.replace(target)
            extracted.append(target)
    return extracted


def extract_files(zip_files: list[Path], output_path: Path) -> list[Path]:
    with ThreadPoolExecutor() as pool:
        results = list(
            tqdm(
                pool.map(lambda z: extract_zip(z, output_path), zip_files),
                total=len(zip_files),
                desc="  extract",
                unit="zip",
            )
        )
    return [f for r in results for f in r]


def prepare_csv_folder(csv_path: Path, month: str) -> None:
    """Make sure the csv folder only holds files from `month` (dbt globs over it)."""
    marker = csv_path / SNAPSHOT_MARKER
    expected = f"{month} {CSV_FORMAT}"
    if csv_path.is_dir() and (not marker.is_file() or marker.read_text().strip() != expected):
        shutil.rmtree(csv_path)
    csv_path.mkdir(parents=True, exist_ok=True)
    marker.write_text(expected)


def snapshot_month(csv_path: Path) -> str | None:
    marker = csv_path / SNAPSHOT_MARKER
    return marker.read_text().split()[0] if marker.is_file() else None


#
# Transformation (dbt)
#


def run_dbt(data_dir: Path, args: list[str] | None = None) -> None:
    """Run `dbt run` on ./dbt against `data_dir` (layers 2 and 3).

    The absolute data path is passed via RFB_CNPJ_DATA_DIR, so the views in the
    DuckDB database point to absolute parquet paths (usable from any tool/folder).
    dbt runs in a subprocess: dbt-duckdb keeps the database open in-process.
    """
    env = {**os.environ, DATA_DIR_ENV: data_dir.resolve().as_posix()}
    cmd = [
        sys.executable, "-c", "from dbt.cli.main import cli; cli()",
        "run", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR), *(args or []),
    ]
    result = subprocess.run(cmd, env=env, cwd=DBT_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"dbt run failed (exit code {result.returncode}).")


#
# Main
#


def build(data_dir: Path, month: str | None, transform: bool = True) -> str:
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        months = available_months(client)
        if month is None:
            month = months[-1]
        elif month not in months:
            raise SystemExit(f"Month {month} not available. Available: {', '.join(months)}")
        print(f"Snapshot: {month}")
        remote = files_to_download(client, month)

    zip_root = data_dir / ZIP_SOURCES
    entries = []
    for e in remote:
        folder = zip_root / ("regime_tributario" if e.path.startswith(REGIME_TRIBUTARIO_FOLDER) else month)
        folder.mkdir(parents=True, exist_ok=True)
        entries.append((e, folder / e.name))

    # L0: Download zip files. (~8 GB)
    print(f"L0: Downloading {len(entries)} files ({sum(e.size for e, _ in entries) / 2**30:.1f} GB)...")
    asyncio.run(download_files(entries))

    # L1: Extract csv files, converted to utf-8. (~25 GB)
    print("L1: Extracting files...")
    csv_path = data_dir / CSV_SOURCES
    prepare_csv_folder(csv_path, month)
    extracted = extract_files([t for _, t in entries], csv_path)
    print(f"    {len(extracted)} csv files ready in {csv_path}.")

    # L2/L3: parquet files and views (dbt).
    (data_dir / PARQUET_SOURCES).mkdir(parents=True, exist_ok=True)
    if transform:
        print("L2: Running dbt...")
        run_dbt(data_dir)
    return month


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", help="Snapshot to download (YYYY-MM). Default: latest available.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Default: ./data")
    parser.add_argument("--list", action="store_true", help="List available snapshots and exit.")
    parser.add_argument("--skip-dbt", action="store_true", help="Only download and extract (layers 0 and 1).")
    args = parser.parse_args(argv)

    if args.list:
        with httpx.Client(timeout=60.0) as client:
            print("\n".join(available_months(client)))
        return

    build(args.data_dir, args.month, transform=not args.skip_dbt)


if __name__ == "__main__":
    main()
