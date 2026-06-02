import duckdb

db_path = r'C:\Users\Samuel\.jachin\client_volumes\bi_data\duckdb\bi.duckdb'
con = duckdb.connect(db_path, read_only=True)

cols_info = con.execute("DESCRIBE bi_stats_user_dau").fetchdf()
col_names = list(cols_info['column_name'])
date_col = col_names[0]
channel_col = col_names[1]
dau_col = col_names[2]

print('=== ALL distinct data-dates in bi_stats_user_dau (any ingestion) ===')
q = f'SELECT DISTINCT "{date_col}" FROM bi_stats_user_dau ORDER BY "{date_col}" DESC LIMIT 10'
result = con.execute(q).fetchall()
print([str(r[0]) for r in result])

print('\n=== Rows where data-date = 2026-05-29 (any ingestion) ===')
q2 = f'SELECT "{date_col}", "{channel_col}", "{dau_col}", _ingested_date FROM bi_stats_user_dau WHERE CAST("{date_col}" AS VARCHAR) = \'2026-05-29\' ORDER BY _ingested_date DESC'
r2 = con.execute(q2).fetchdf()
r2.columns = ['data_date', 'channel', 'dau', '_ingested_date']
print(r2.to_string() if len(r2) > 0 else 'NO DATA for 2026-05-29')

print('\n=== Rows where data-date = 2026-05-28, ALL渠道 (全渠道 = all channels) ===')
q3 = f'SELECT "{date_col}", "{channel_col}", "{dau_col}", _ingested_date FROM bi_stats_user_dau WHERE CAST("{date_col}" AS VARCHAR) = \'2026-05-28\' ORDER BY _ingested_date DESC LIMIT 5'
r3 = con.execute(q3).fetchdf()
r3.columns = ['data_date', 'channel', 'dau', '_ingested_date']
print(r3.to_string() if len(r3) > 0 else 'NO DATA for 2026-05-28')

# Also check daily_ops_summary for 5/29
cols_ops = con.execute("DESCRIBE bi_daily_ops_summary").fetchdf()
ops_cols = list(cols_ops['column_name'])
ops_date_col = ops_cols[0]
ops_dau_col = ops_cols[1]

print('\n=== bi_daily_ops_summary: all distinct data-dates ===')
q4 = f'SELECT DISTINCT "{ops_date_col}", "{ops_dau_col}" FROM bi_daily_ops_summary ORDER BY "{ops_date_col}" DESC LIMIT 10'
r4 = con.execute(q4).fetchdf()
r4.columns = ['data_date', 'dau']
print(r4.to_string())
