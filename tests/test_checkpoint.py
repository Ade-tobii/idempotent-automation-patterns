import csv

from patterns.checkpoint import Checkpoint, iter_chunks


def test_checkpoint_persists_across_instances(tmp_path):
    path = tmp_path / "cp.json"
    cp = Checkpoint(path)
    cp.mark_done("FAC-01")
    cp.set("rows_done", 500)

    reloaded = Checkpoint(path)
    assert reloaded.is_done("FAC-01") and not reloaded.is_done("FAC-02")
    assert reloaded.get("rows_done") == 500
    assert not (tmp_path / "cp.json.tmp").exists()


def test_iter_chunks_resumes_from_offset(tmp_path):
    path = tmp_path / "data.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id"])
        w.writerows([[i] for i in range(10)])

    chunks = list(iter_chunks(path, chunk_size=4))
    assert [offset for offset, _ in chunks] == [4, 8, 10]

    resumed = list(iter_chunks(path, chunk_size=4, start_row=8))
    assert resumed == [(10, [{"id": "8"}, {"id": "9"}])]
