"""Parse CCITTFaxDecode stream metadata for visualization.

Returns parameter metadata and a structure map for rendering a colour-coded
bar and an annotated parameter table — analogous to what jpeg.py does for
JPEG streams.  CCITT Fax is a pure variable-length bit stream with no
byte-level segment markers, so the structure is a single coloured block
representing the compressed data, with the PDF DecodeParms providing all
the descriptive metadata.
"""
from __future__ import annotations


def parse_ccitt(
    raw_data: bytes,
    *,
    k: int = -1,
    columns: int = 1728,
    rows: int | None = None,
    end_of_block: bool = True,
    end_of_line: bool = False,
    encoded_byte_align: bool = False,
    black_is_1: bool = False,
    damaged_rows_before_error: int = 0,
) -> dict:
    """Return structured CCITTFaxDecode metadata for display.

    Parameters mirror PDF DecodeParms for CCITTFaxDecode:
      k                       < 0  → Group 4 (T.6)
                                0  → Group 3 1D (T.4)
                              > 0  → Group 3 2D (T.4)
      columns                 pixels per line (default 1728)
      rows                    number of rows if given in DecodeParms
      end_of_block            stream terminated by EOFB / RTC (default true)
      end_of_line             EOL bit patterns present between rows (default false)
      encoded_byte_align      rows byte-aligned (default false)
      black_is_1              bit value 1 represents black (default false)
      damaged_rows_before_error  tolerance for malformed rows (default 0)
    """
    if k < 0:
        compression_name = 'CCITT Group 4 / T.6'
        compression_short = 'G4'
        standard = 'ITU-T T.6'
    elif k == 0:
        compression_name = 'CCITT Group 3 1D / T.4'
        compression_short = 'G3-1D'
        standard = 'ITU-T T.4'
    else:
        compression_name = f'CCITT Group 3 2D / T.4 (K={k})'
        compression_short = 'G3-2D'
        standard = 'ITU-T T.4'

    raw_size = len(raw_data)

    # Build parameter rows for tabular display
    params: list[dict] = [
        {
            'key': 'K',
            'value': str(k),
            'meaning': compression_name,
        },
        {
            'key': 'Columns',
            'value': str(columns),
            'meaning': f'{columns} pixels per scan line',
        },
    ]
    if rows is not None:
        params.append({'key': 'Rows', 'value': str(rows), 'meaning': f'{rows} scan lines'})
    params += [
        {
            'key': 'EndOfBlock',
            'value': 'true' if end_of_block else 'false',
            'meaning': 'stream ends with EOFB / RTC' if end_of_block else 'no end-of-block code',
        },
        {
            'key': 'EndOfLine',
            'value': 'true' if end_of_line else 'false',
            'meaning': 'EOL codes present between lines' if end_of_line else 'no EOL codes between lines',
        },
        {
            'key': 'EncodedByteAlign',
            'value': 'true' if encoded_byte_align else 'false',
            'meaning': 'each line starts on byte boundary' if encoded_byte_align else 'no byte alignment',
        },
        {
            'key': 'BlackIs1',
            'value': 'true' if black_is_1 else 'false',
            'meaning': '1 = black, 0 = white' if black_is_1 else '0 = black, 1 = white (default)',
        },
    ]
    if damaged_rows_before_error:
        params.append({
            'key': 'DamagedRowsBeforeError',
            'value': str(damaged_rows_before_error),
            'meaning': f'tolerate up to {damaged_rows_before_error} damaged row(s)',
        })

    # Structure bar: single block for the compressed fax data
    structure: list[dict] = [
        {
            'label': f'{compression_name}  —  {raw_size} compressed bytes',
            'offset': 0,
            'size': raw_size,
            'color': 'ccitt',
        }
    ]

    return {
        'k': k,
        'columns': columns,
        'rows': rows,
        'end_of_block': end_of_block,
        'end_of_line': end_of_line,
        'encoded_byte_align': encoded_byte_align,
        'black_is_1': black_is_1,
        'damaged_rows_before_error': damaged_rows_before_error,
        'compression_name': compression_name,
        'compression_short': compression_short,
        'standard': standard,
        'params': params,
        'raw_size': raw_size,
        'structure': structure,
    }
