# 中文版是从英文版生成的

`JpgFindRaw_zh.py` 由英文版 `../jpg-find-raw-en/JpgFindRaw.py` 自动生成（翻译界面 + 不保存路径 + 过滤中文选项）。
修改英文版后，在仓库根目录执行以下命令重新生成中文版：

    python3 jpg-find-raw-zh/tools/make_zh.py jpg-find-raw-en/JpgFindRaw.py jpg-find-raw-zh/JpgFindRaw_zh.py
    python3 jpg-find-raw-zh/tools/patch_zh_extra.py jpg-find-raw-zh/JpgFindRaw_zh.py

每条替换都会校验，如果英文版改动导致某处匹配不上，脚本会报错并指出是哪一句。
