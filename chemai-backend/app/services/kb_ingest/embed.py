"""嵌入器（T9）：FakeEmbedder（确定性，测试与 --fake 冒烟）/ LocalEmbedder（本地 bge）。

本地编码器复用 vector_search.get_text_encoder()（SentenceTransformer + 本地模型目录，
1024 维）——零 API 成本，与相似题检索同一模型栈。SiliconFlow/ DashScope 留接口位，
第一期不接。
"""
import hashlib


class FakeEmbedder:
    """确定性伪向量：文本 sha256 → 固定维度的可重复向量。只用于离线冒烟与测试，
    语义检索不代表真实效果（检索质量必须用真模型回归）。"""

    def __init__(self, dim: int = 32):
        self.dim = dim

    def embed(self, texts):
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [(h[i % len(h)] / 255.0) - 0.5 for i in range(self.dim)]
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class LocalEmbedder:
    """本地 bge（vector_search 的编码器单例）。批量编码，失败向上抛。"""

    def __init__(self, batch_size: int = 32):
        from app.services.vector_search import get_text_encoder
        self._encoder = get_text_encoder()
        self.batch_size = batch_size

    def embed(self, texts):
        return [v.tolist() for v in self._encoder.encode(
            list(texts), batch_size=self.batch_size, show_progress_bar=False)]
