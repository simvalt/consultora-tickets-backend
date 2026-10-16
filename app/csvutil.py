import csv
import io
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime

from fastapi.responses import StreamingResponse

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> str:
    """Convierte a texto y neutraliza fórmulas de hoja de cálculo (CSV injection)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    return "'" + text if text.startswith(_DANGEROUS) else text


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
