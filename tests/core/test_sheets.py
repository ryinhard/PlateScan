"""app.core.sheets 單元測試：users / buffer 工作表讀寫邏輯與 Async Lock 防護。

不連線真實 Google Sheets API，改以 FakeWorksheet 模擬 gspread.Worksheet
的最小介面（get_all_values / append_row / find / update / delete_rows），
專注驗證本模組的資料轉換邏輯與並行安全性。
"""

import asyncio
import time

import pytest

from app.core import sheets


class FakeCell:
    def __init__(self, row: int) -> None:
        self.row = row


class FakeWorksheet:
    """以記憶體中的 list[list[str]] 模擬 gspread.Worksheet，rows[0] 為標題列。"""

    def __init__(self, header: list[str]) -> None:
        self.rows: list[list[str]] = [header]

    def get_all_values(self) -> list[list[str]]:
        return [list(row) for row in self.rows]

    def append_row(self, row: list[str]) -> None:
        self.rows.append(list(row))

    def find(self, query: str, in_column: int = 1) -> "FakeCell | None":
        col_idx = in_column - 1
        for idx, row in enumerate(self.rows):
            if idx == 0:
                continue
            if col_idx < len(row) and row[col_idx] == query:
                return FakeCell(row=idx + 1)  # gspread 列號從 1 起算
        return None

    def update(self, range_name: str, values: list[list[str]]) -> None:
        start_cell = range_name.split(":")[0]
        col_letter = start_cell[0]
        row_num = int(start_cell[1:])
        col_idx = ord(col_letter) - ord("A")
        row = self.rows[row_num - 1]
        for offset, value in enumerate(values[0]):
            target_idx = col_idx + offset
            while len(row) <= target_idx:
                row.append("")
            row[target_idx] = value

    def delete_rows(self, row_num: int) -> None:
        del self.rows[row_num - 1]


@pytest.fixture(autouse=True)
def _reset_locks():
    """每個測試前清空全域 lock 字典，避免測試間互相干擾。"""
    sheets._locks.clear()
    yield
    sheets._locks.clear()


@pytest.fixture()
def fake_users_ws(monkeypatch: pytest.MonkeyPatch) -> FakeWorksheet:
    ws = FakeWorksheet(["user_key", "display_name", "google_sheet_id", "is_active", "created_at"])
    monkeypatch.setattr(sheets, "_get_worksheet", lambda name: ws)
    return ws


@pytest.fixture()
def fake_buffer_ws(monkeypatch: pytest.MonkeyPatch) -> FakeWorksheet:
    ws = FakeWorksheet(["user_key", "item_type", "content", "created_at"])
    monkeypatch.setattr(sheets, "_get_worksheet", lambda name: ws)
    return ws


# --- users 工作表 ---


async def test_get_user_returns_none_when_not_found(fake_users_ws: FakeWorksheet):
    assert await sheets.get_user("line:U404") is None


async def test_get_user_parses_matching_row(fake_users_ws: FakeWorksheet):
    fake_users_ws.rows.append(["line:U1", "小明", "sheet-abc", "TRUE", "2026/08/14"])

    user = await sheets.get_user("line:U1")

    assert user == {
        "user_key": "line:U1",
        "display_name": "小明",
        "google_sheet_id": "sheet-abc",
        "is_active": True,
        "created_at": "2026/08/14",
    }


async def test_upsert_user_appends_new_row_when_absent(fake_users_ws: FakeWorksheet):
    await sheets.upsert_user("tg:U2", google_sheet_id="sheet-xyz", display_name="小華")

    assert len(fake_users_ws.rows) == 2
    row = fake_users_ws.rows[1]
    assert row[0] == "tg:U2"
    assert row[1] == "小華"
    assert row[2] == "sheet-xyz"
    assert row[3] == "TRUE"


async def test_upsert_user_updates_existing_row_in_place(fake_users_ws: FakeWorksheet):
    fake_users_ws.rows.append(["line:U1", "小明", "sheet-old", "TRUE", "2026/08/01"])

    await sheets.upsert_user("line:U1", google_sheet_id="sheet-new", display_name="小明")

    assert len(fake_users_ws.rows) == 2  # 沒有新增列，而是原地更新
    row = fake_users_ws.rows[1]
    assert row[0] == "line:U1"
    assert row[2] == "sheet-new"


# --- buffer 工作表 ---


async def test_append_and_get_buffer_items_filters_by_user_and_skips_header(
    fake_buffer_ws: FakeWorksheet,
):
    await sheets.append_buffer_item("line:U1", "photo", "msg-1")
    await sheets.append_buffer_item("tg:U2", "photo", "file-9")
    await sheets.append_buffer_item("line:U1", "photo", "msg-2")

    items = await sheets.get_buffer_items("line:U1")

    assert [item["content"] for item in items] == ["msg-1", "msg-2"]
    assert all(item["user_key"] == "line:U1" for item in items)
    assert all(item["item_type"] == "photo" for item in items)


async def test_clear_buffer_removes_only_target_user_rows(fake_buffer_ws: FakeWorksheet):
    await sheets.append_buffer_item("line:U1", "photo", "msg-1")
    await sheets.append_buffer_item("tg:U2", "photo", "file-9")
    await sheets.append_buffer_item("line:U1", "photo", "msg-2")

    await sheets.clear_buffer("line:U1")

    remaining = fake_buffer_ws.rows[1:]
    assert len(remaining) == 1
    assert remaining[0][0] == "tg:U2"


# --- Async Lock：防範競態條件 ---


async def test_get_lock_returns_same_instance_for_same_user_key():
    lock_a1 = await sheets._get_lock("line:U1")
    lock_a2 = await sheets._get_lock("line:U1")
    assert lock_a1 is lock_a2


async def test_get_lock_returns_different_instance_for_different_user_key():
    lock_a = await sheets._get_lock("line:U1")
    lock_b = await sheets._get_lock("tg:U2")
    assert lock_a is not lock_b


async def test_get_lock_concurrent_creation_is_race_free():
    """多個並行事件同時第一次取得同一 user_key 的 lock 時，仍須拿到同一實例。"""
    results = await asyncio.gather(*[sheets._get_lock("line:U1") for _ in range(20)])
    assert len({id(lock) for lock in results}) == 1


async def test_append_buffer_item_holds_lock_during_write(
    fake_buffer_ws: FakeWorksheet,
):
    lock = await sheets._get_lock("line:U1")
    observed_locked_during_write = False

    original_append_row = fake_buffer_ws.append_row

    def spy_append_row(row: list[str]) -> None:
        nonlocal observed_locked_during_write
        observed_locked_during_write = lock.locked()
        original_append_row(row)

    fake_buffer_ws.append_row = spy_append_row  # type: ignore[method-assign]

    await sheets.append_buffer_item("line:U1", "photo", "msg-1")

    assert observed_locked_during_write is True
    assert lock.locked() is False  # 執行完畢後鎖應已釋放


async def test_concurrent_buffer_writes_for_same_user_are_serialized(
    fake_buffer_ws: FakeWorksheet,
):
    """同一 user_key 連續傳送多張照片時，寫入應序列化、不重疊，避免遺漏暫存資料。"""
    intervals: list[tuple[float, float]] = []
    original_append_row = fake_buffer_ws.append_row

    def slow_append_row(row: list[str]) -> None:
        start = time.monotonic()
        time.sleep(0.05)
        intervals.append((start, time.monotonic()))
        original_append_row(row)

    fake_buffer_ws.append_row = slow_append_row  # type: ignore[method-assign]

    await asyncio.gather(
        sheets.append_buffer_item("line:U1", "photo", "m1"),
        sheets.append_buffer_item("line:U1", "photo", "m2"),
        sheets.append_buffer_item("line:U1", "photo", "m3"),
    )

    intervals.sort()
    for (_, end_prev), (start_next, _) in zip(intervals, intervals[1:]):
        assert end_prev <= start_next  # 寫入時間區間不得重疊

    assert len(fake_buffer_ws.rows) - 1 == 3


async def test_concurrent_buffer_writes_for_different_users_are_not_serialized(
    fake_buffer_ws: FakeWorksheet,
):
    """不同 user_key 之間不應共用同一把鎖，避免無謂地互相阻塞。"""
    barrier = asyncio.Event()
    entered = 0
    entered_lock = asyncio.Lock()

    original_append_row = fake_buffer_ws.append_row

    def blocking_append_row(row: list[str]) -> None:
        nonlocal entered
        entered += 1
        original_append_row(row)

    fake_buffer_ws.append_row = blocking_append_row  # type: ignore[method-assign]

    lock_1 = await sheets._get_lock("line:U1")
    lock_2 = await sheets._get_lock("tg:U2")

    async def hold_lock(lock: asyncio.Lock) -> None:
        async with lock:
            await barrier.wait()

    holder_task = asyncio.create_task(hold_lock(lock_1))
    await asyncio.sleep(0)  # 確保 holder_task 已取得 lock_1

    # lock_2（不同使用者）此時應不受 lock_1 影響，可立即完成寫入。
    await asyncio.wait_for(
        sheets.append_buffer_item("tg:U2", "photo", "file-1"), timeout=1
    )

    barrier.set()
    await holder_task
