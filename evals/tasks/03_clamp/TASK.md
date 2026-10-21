Fix `clamp` in `limits.py` so values below the lower bound become the lower
bound, values above the upper bound become the upper bound, and in-range values
are unchanged. Assume `lower <= upper`.
