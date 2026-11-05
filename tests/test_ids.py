"""Reading identifier CSVs: the chunked, filtered read."""

from pipeline.ids import read_id_csv_rows

BIG_CN = "1234567890123456789"   # a 19-digit PLT_CN that a float would round


def test_read_id_csv_rows_keeps_only_the_wanted_keys_a_chunk_at_a_time(tmp_path):
    path = tmp_path / "trees.csv"
    path.write_text("TM_ID,PLT_CN,DIA\n"
                    f"1,{BIG_CN},4.0\n"
                    "2,11,5.0\n"
                    f"3,{BIG_CN},6.0\n"
                    "1,12,7.0\n"
                    "4,13,8.0\n")
    rows = read_id_csv_rows(path, "TM_ID", {"1", "4"}, chunksize=2, usecols=["TM_ID", "PLT_CN", "DIA"])
    assert rows["TM_ID"].tolist() == ["1", "1", "4"]
    assert rows["PLT_CN"].tolist() == [BIG_CN, "12", "13"]       # exact strings, digit for digit
    assert rows["DIA"].tolist() == [4.0, 7.0, 8.0]


def test_read_id_csv_rows_with_no_match_is_an_empty_frame_with_the_columns(tmp_path):
    path = tmp_path / "trees.csv"
    path.write_text("TM_ID,PLT_CN,DIA\n2,11,5.0\n")
    rows = read_id_csv_rows(path, "TM_ID", set(), chunksize=1)
    assert rows.empty
    assert list(rows.columns) == ["TM_ID", "PLT_CN", "DIA"]
