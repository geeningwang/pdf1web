"""
FlateDecode image metadata for visualization.
"""
from __future__ import annotations

_PREDICTOR_NAMES: dict[int, tuple[str, str]] = {
    1:  ("None",         "No prediction applied"),
    2:  ("TIFF",         "TIFF predictor 2 (horizontal differencing)"),
    10: ("PNG None",     "PNG filter: no transformation"),
    11: ("PNG Sub",      "PNG filter: subtract left neighbor (horizontal diff)"),
    12: ("PNG Up",       "PNG filter: subtract the pixel above (vertical diff) — most common"),
    13: ("PNG Average",  "PNG filter: average of left and above pixels"),
    14: ("PNG Paeth",    "PNG filter: Paeth predictor using left, above, and upper-left"),
    15: ("PNG Optimum",  "PNG filter: encoder selects the best filter per row"),
}


def parse_flat_image(
    raw_data: bytes,
    *,
    predictor: int = 1,
    columns: int | None = None,
    colors: int = 1,
    bpc: int = 8,
) -> dict:
    """Return FlateDecode visualization metadata (mirrors parse_ccitt structure)."""

    pred_name, pred_meaning = _PREDICTOR_NAMES.get(
        predictor, (f"Predictor {predictor}", "Unknown predictor value")
    )

    params: list[dict] = [
        {
            "key": "Predictor",
            "value": str(predictor),
            "meaning": f"{pred_name} — {pred_meaning}",
        },
    ]
    if predictor != 1:
        if columns is not None:
            params.append({
                "key": "Columns",
                "value": str(columns),
                "meaning": f"Samples per row used for prediction ({columns})",
            })
        params.append({
            "key": "Colors",
            "value": str(colors),
            "meaning": f"Color components per sample ({colors} channel{'s' if colors != 1 else ''})",
        })
        params.append({
            "key": "BitsPerComponent",
            "value": str(bpc),
            "meaning": f"{bpc} bits per color component",
        })

    raw_size = len(raw_data)

    structure = [
        {
            "label": "Compressed pixel data (Deflate/zlib)",
            "offset": 0,
            "size": raw_size,
            "color": "flat",
        }
    ]

    return {
        "predictor": predictor,
        "predictor_name": pred_name,
        "columns": columns,
        "colors": colors,
        "bpc": bpc,
        "raw_size": raw_size,
        "params": params,
        "structure": structure,
    }
