import csv
import io
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime

from fastapi.responses import StreamingResponse

_DANGEROUS = "=+-@\t\r"  # primeros caracteres que una hoja de cálculo interpreta como fórmula


def _neutralize(text: str) -> str:
    return "'" + text if text and text[0] in _DANGEROUS else text


def safe_cell(value: object) -> str:
    """Convierte a texto y neutraliza fórmulas de hoja de cálculo (CSV injection).

    Ruta rápida por tipo exacto: se llama una vez por celda (millones de veces en exportaciones grandes).
    """
    kind = type(value)
    if kind is str:
        return _neutralize(value)  # type: ignore[arg-type]
    if value is None:
        return ""
    if kind is bool:
        return "Sí" if value else "No"
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset():
            value = value.astimezone(UTC)
        return value.isoformat(timespec="seconds").replace("+00:00", "Z") + ("" if value.tzinfo else "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int | float):
        return str(value)
    return _neutralize(str(value))


def csv_stream(headers: list[str], rows: Iterable[list[object]]) -> Iterator[bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    yield b"\xef\xbb\xbf"  # BOM UTF-8 para que Excel respete los acentos
    writer.writerow(headers)
    for n, row in enumerate(rows, 1):
        writer.writerow([safe_cell(v) for v in row])
        if n % 500 == 0:
            yield buf.getvalue().encode()
            buf.seek(0)
            buf.truncate(0)
    yield buf.getvalue().encode()


def csv_response(filename: str, headers: list[str], rows: Iterable[list[object]]) -> StreamingResponse:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        csv_stream(headers, rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}-{stamp}.csv"', "Cache-Control": "no-store"},
    )
