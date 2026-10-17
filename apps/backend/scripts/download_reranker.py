r"""下载可选的 BGE-Reranker 模型到用户配置的缓存目录。

使用：
    cd apps/backend
    python scripts/download_reranker.py

会做的事：
  1. 使用 HF_HOME；未配置时使用用户目录下的标准缓存
  2. 下载 BAAI/bge-reranker-v2-m3 (~278MB) 到上述目录
  3. 跑一次 rerank 冒烟测试，确保模型能加载 + 推理
  4. 提示在 settings 里打开 ENABLE_RERANKER=True
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    # 1. 尊重调用者配置，默认使用当前用户缓存目录
    hf_home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    Path(hf_home).mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = hf_home
    os.environ["HF_HUB_CACHE"] = hf_home  # transformers v4.37+ 用这个
    print(f"[download_reranker] HF_HOME = {hf_home}")

    # 2. 安装依赖（若缺失）
    try:
        from FlagEmbedding import FlagReranker  # type: ignore
    except ImportError:
        print("[download_reranker] FlagEmbedding 未安装，自动安装中...")
        import subprocess
        rc = subprocess.call([sys.executable, "-m", "pip", "install", "FlagEmbedding"])
        if rc != 0:
            print("[download_reranker] pip install FlagEmbedding 失败")
            return rc
        from FlagEmbedding import FlagReranker  # type: ignore

    # 3. 下载模型
    model_name = "BAAI/bge-reranker-v2-m3"
    print(f"[download_reranker] 下载/加载 {model_name}（约 278MB）...")
    reranker = FlagReranker(model_name, use_fp16=True)

    # 4. 冒烟测试
    print("[download_reranker] 冒烟测试：rerank 3 条候选...")
    scores = reranker.compute_score(
        [
            ["BTC 下周怎么看", "比特币链上数据显示鲸鱼积累"],
            ["BTC 下周怎么看", "天气预报明天有雨"],
            ["BTC 下周怎么看", "加密货币短期震荡概率较大"],
        ],
        normalize=True,
    )
    print(f"[download_reranker] scores = {scores}")
    if not isinstance(scores, list) or len(scores) != 3:
        print("[download_reranker] ❌ 冒烟测试异常")
        return 1

    print("[download_reranker] ✅ 模型已就绪。在 settings 里打开：")
    print("    ENABLE_RERANKER=True")
    print("或通过环境变量：")
    print("    $env:ENABLE_RERANKER = 'True'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
