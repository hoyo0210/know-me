"""
Know Me — E01 知识管道与索引（加载、切分、嵌入、写入向量库）。

包内模块导读：
- settings：环境变量与路径
- types / loaders / splitting：从文件到 TextChunk
- embeddings / chroma_store：从文本到向量 + 持久化
- pipeline：串联上述步骤
- cli：命令行
"""

__version__ = "0.1.0"
