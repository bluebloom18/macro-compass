"""Calendar alignment helpers: forward-fill each series onto a master date axis,
mirroring how request.security('D', ...) holds the last confirmed value between prints."""


def to_map(series):
    return {row["date"]: row for row in series}


def forward_fill_onto(series, master_dates):
    """Align `series` (list of {date, o,h,l,c}) onto `master_dates` (sorted list of date strings),
    carrying forward the last known bar for any master date that falls between prints of `series`.
    Returns a list (same length as master_dates) of row-dict-or-None."""
    smap = to_map(series)
    sdates = sorted(smap.keys())
    out = [None] * len(master_dates)
    si = -1  # index into sdates of the latest date <= current master date
    ptr = 0
    n = len(sdates)
    last_row = None
    for i, d in enumerate(master_dates):
        while ptr < n and sdates[ptr] <= d:
            last_row = smap[sdates[ptr]]
            ptr += 1
        out[i] = last_row
    return out


def forward_fill_join(series_list):
    """Build a master calendar = union of all dates across series_list, then forward-fill each
    series onto it. Returns list of (date, tuple_of_rows_or_None) sorted by date."""
    all_dates = sorted(set(d["date"] for s in series_list for d in s))
    aligned = [forward_fill_onto(s, all_dates) for s in series_list]
    return list(zip(all_dates, zip(*aligned)))
