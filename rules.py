"""Row filters applied inside DuckDB. Values are per 100 g or 100 ml."""

# Energy check: fiber counts 2 kcal/g instead of 4 and alcohol adds 7 kcal/g, so beer, wine,
# spirits and bran cereals survive. Band: the larger of 30 percent and 25 kcal.
PLAUSIBLE = """
  n1008 IS NOT NULL AND n1008 BETWEEN 0 AND 950
  AND (n1003 IS NULL OR n1003 BETWEEN 0 AND 100)
  AND (n1005 IS NULL OR n1005 BETWEEN 0 AND 100)
  AND (n1004 IS NULL OR n1004 BETWEEN 0 AND 100)
  AND (n1079 IS NULL OR n1079 BETWEEN 0 AND 100)
  AND (n1018 IS NULL OR n1018 BETWEEN 0 AND 100)
  AND coalesce(n1003, 0) + coalesce(n1005, 0) + coalesce(n1004, 0) <= 105
  AND (n1003 IS NULL OR n1005 IS NULL OR n1004 IS NULL
       OR abs(4 * n1003 + 4 * greatest(n1005 - coalesce(n1079, 0), 0) + 2 * coalesce(n1079, 0)
              + 9 * n1004 + 7 * coalesce(n1018, 0) - n1008) <= greatest(0.30 * n1008, 25))
"""
