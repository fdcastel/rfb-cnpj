import asyncio
import hashlib
import zipfile
from pathlib import Path

import httpx
import pytest
from tqdm import tqdm

from rfb_cnpj import build

FIXTURES = Path(__file__).parent / "fixtures"


#
# Offline
#


def test_parse_propfind_recorded_response():
    """Real PROPFIND response recorded from the RFB share (2026-11-11)."""
    xml = (FIXTURES / "propfind_cnpj_2026-10.xml").read_text(encoding="utf-8")
    entries = build.parse_propfind(xml, "Dados/Cadastros/CNPJ/2026-10/")
    names = {e.name: e for e in entries}

    assert "2026-10" not in names  # folder itself is excluded
    assert len(entries) == 37
    assert names["Cnaes.zip"].size == 22078
    assert names["Estabelecimentos0.zip"].path == "Dados/Cadastros/CNPJ/2026-10/Estabelecimentos0.zip"
    assert not names["Estabelecimentos0.zip"].is_folder
    assert names["Estabelecimentos0.zip"].size > 2_000_000_000


def test_extract_zip_converts_to_utf8_and_is_idempotent(tmp_path):
    # Real content, incl. a stray C1 byte (0x8f) seen in the 2026-10 ESTABELE files.
    text = '"3250709";"Serviço de laboratório óptico"\n"x";"\x8f"\n'
    payload = text.encode("latin-1")
    zip_file = tmp_path / "x.zip"
    with zipfile.ZipFile(zip_file, "w") as z:
        z.writestr("F.K03200$Z.D61010.CNAECSV", payload)
    out = tmp_path / "csv"
    out.mkdir()

    [target] = build.extract_zip(zip_file, out)
    assert target.read_bytes() == text.encode("utf-8")
    assert not list(out.glob("*.part"))

    mtime = target.stat().st_mtime_ns
    build.extract_zip(zip_file, out)
    assert target.stat().st_mtime_ns == mtime  # not extracted again


def test_prepare_csv_folder_clears_other_snapshots(tmp_path):
    csv_path = tmp_path / "1-csv_sources"
    build.prepare_csv_folder(csv_path, "2026-09")
    (csv_path / "old.ESTABELE").write_text("x")

    build.prepare_csv_folder(csv_path, "2026-09")
    assert (csv_path / "old.ESTABELE").exists()  # same month: kept

    build.prepare_csv_folder(csv_path, "2026-10")
    assert not (csv_path / "old.ESTABELE").exists()  # new month: cleared
    assert build.snapshot_month(csv_path) == "2026-10"

    # A csv layer written in another format (e.g. v1, latin-1) is discarded too.
    (csv_path / build.SNAPSHOT_MARKER).write_text("2026-10")
    (csv_path / "v1.ESTABELE").write_text("x")
    build.prepare_csv_folder(csv_path, "2026-10")
    assert not (csv_path / "v1.ESTABELE").exists()


#
# Live RFB server
#


@pytest.fixture(scope="module")
def client():
    with httpx.Client(timeout=60.0, follow_redirects=True) as c:
        yield c


@pytest.mark.network
def test_available_months(client):
    months = build.available_months(client)
    assert "2024-12" in months
    assert months == sorted(months)
    assert months[-1] >= "2026-10"


@pytest.mark.network
def test_files_to_download(client):
    month = build.available_months(client)[-1]
    names = {e.name for e in build.files_to_download(client, month)}

    expected = {f"Empresas{i}.zip" for i in range(10)} | {f"Estabelecimentos{i}.zip" for i in range(10)}
    expected |= {"Cnaes.zip", "Motivos.zip", "Municipios.zip", "Naturezas.zip", "Paises.zip", "Simples.zip"}
    expected |= {
        "entidades-imunes-e-isentas.zip",
        "entidades-lucro-arbitrado.zip",
        "entidades-lucro-presumido.zip",
        "entidades-lucro-real.zip",
    }
    assert expected <= names
    assert not any(n.startswith("Socios") for n in names)


def _download(entry, target):
    with tqdm(disable=True) as progress:
        asyncio.run(build.download_files([(entry, target)]))


@pytest.mark.network
def test_download_and_resume(client, tmp_path):
    [entry] = [e for e in build.files_to_download(client, "2026-10") if e.name == "Cnaes.zip"]

    full = tmp_path / "full" / "Cnaes.zip"
    full.parent.mkdir()
    _download(entry, full)
    assert full.stat().st_size == entry.size
    with zipfile.ZipFile(full) as z:
        [member] = z.namelist()
        assert member.endswith(".CNAECSV")
        assert b'"4774100";"Com\xe9rcio varejista de artigos de \xf3ptica"' in z.read(member)

    data = full.read_bytes()
    half = len(data) // 2

    # Resume: a partial file holding the first half is completed with an HTTP Range request.
    resumed = tmp_path / "resumed" / "Cnaes.zip"
    resumed.parent.mkdir()
    (resumed.parent / "Cnaes.zip.part").write_bytes(data[:half])
    _download(entry, resumed)
    assert hashlib.sha256(resumed.read_bytes()).digest() == hashlib.sha256(data).digest()
    assert not (resumed.parent / "Cnaes.zip.part").exists()

    # Proof that only the missing bytes were requested: a marker-filled first half survives.
    marked = tmp_path / "marked" / "Cnaes.zip"
    marked.parent.mkdir()
    (marked.parent / "Cnaes.zip.part").write_bytes(b"\xAA" * half)
    _download(entry, marked)
    assert marked.read_bytes() == b"\xAA" * half + data[half:]
