"""KB2b：从知识点文档「## 常见易错点」提取 {wrong, right, why} 三元。

契约（设计 Q4）：解析失败降级空列表，绝不阻塞入库；提取结果存
kb_frontmatter.misconceptions_json，出题 hint 与检索增强共用。

当前语料格式（78 篇、345 条）统一为：
  - **"错误说法"**：错。<正确说法>。错因是<原因>。
变体：判定词可能是「不严谨/不准确/反了/片面/不规范」，也可能没有错因句。
解析器只认「粗体 + 全角冒号 + 列表项」，其余静默跳过。
"""
import re

_SECTION = re.compile(r"^##\s+常见易错点\s*$\n([\s\S]*?)(?=^##\s|\Z)", re.M)
_BULLET = re.compile(r'^\s*-\s+\*\*(.+?)\*\*\s*[：:]\s*(.+)$')
_WHY = re.compile(r"错因(?:是|：|:)\s*(.+)$")
_JUDGE = re.compile(r"^(?:错|低效|不严谨|不准确|反了|片面|不规范|不能)。\s*")


def parse_misconceptions(markdown: str) -> list:
    """返回 [{"wrong","right","why"}]；无该小节/格式不符 → []。"""
    try:
        section = _SECTION.search(markdown or "")
        if not section:
            return []
        items = []
        for line in section.group(1).splitlines():
            bullet = _BULLET.match(line)
            if not bullet:
                continue
            wrong = bullet.group(1).strip().strip('"“”').strip()
            rest = bullet.group(2).strip()
            why = ""
            found = _WHY.search(rest)
            if found:
                why = found.group(1).strip().rstrip("。")
                rest = rest[:found.start()].strip()
            right = _JUDGE.sub("", rest).strip().rstrip("。").strip()
            if wrong and right:
                items.append({"wrong": wrong, "right": right, "why": why})
        return items
    except Exception:
        return []
