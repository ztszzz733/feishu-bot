import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bot import ChatHistory, DedupCache, chunk_text, extract_text


class M:
    def __init__(self, key):
        self.key = key


assert extract_text(json.dumps({"text": "@_user_1 你好啊"}), [M("@_user_1")]) == "你好啊"
print("mention strip      OK")

assert extract_text(json.dumps({"text": "hi"}), []) == "hi"
print("plain text         OK")

assert extract_text("not json", []) == ""
assert extract_text("", []) == ""
print("bad json           OK")

assert (
    extract_text(
        json.dumps({"text": "@_user_1 @_user_2 开会"}), [M("@_user_1"), M("@_user_2")]
    )
    == "开会"
)
print("multi mention      OK")

long_text = "\n".join(f"第 {i} 行内容" for i in range(1000))
parts = chunk_text(long_text, 100)
assert all(len(p) <= 100 for p in parts), "chunk 超长"
assert "".join(parts).replace("\n", "") == long_text.replace("\n", ""), "chunk 丢内容"
assert chunk_text("short", 100) == ["short"]
print(f"chunking           OK ({len(parts)} parts)")

d = DedupCache(ttl=60)
assert d.first_time("a") is True
assert d.first_time("a") is False
assert d.first_time("b") is True
print("dedup              OK")

h = ChatHistory(2)
for i in range(6):
    h.append("c", "user" if i % 2 == 0 else "assistant", str(i))
got = h.get("c")
assert len(got) == 4, len(got)
assert got[-1]["content"] == "5"
assert h.get("other") == []
h.reset("c")
assert h.get("c") == []
print("history            OK")

print()
print("ALL PASS")