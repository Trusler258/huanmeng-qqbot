# Minecraft 错误报告分析（2026-10-02 新增）
# 热匹配关键词命中后整章注入 user 消息（不污染 system 缓存）。

## mc_error
@skill: Minecraft 错误诊断 | minecraft,mc,crash,崩溃,报错,错误报告,报错日志,mod,模组,fabric,forge,latest.log,crash-report,hs_err,不兼容,incompatible,exception,堆栈,stacktrace,闪退,启动失败,modrinth,curseforge | 用户粘贴 Minecraft/Java 报错、崩溃日志或问模组冲突时用
【Minecraft 错误诊断】
1. 先分类：网络下载类（连接失败/超时/SSL握手）、模组冲突类（incompatible + 版本对照）、Java内存类（OutOfMemory/hs_err）、游戏崩溃类（生成了 crash-report）。
2. 完整诊断要看三个位置：logs/latest.log（启动全程+首次报错）、crash-reports/ 目录下最新的 crash-*.txt（崩溃时自动生成，含完整堆栈与模组清单）、hs_err_pid 开头的文件（JVM 级崩溃才有）。
3. 用户只贴了片段时，主动引导把 crash-reports 最新 txt 或 latest.log 的报错段落发来——片段只能猜，全文才能定位根因。
4. 模组冲突先对版本：让用户报齐模组名称+版本号，本体与扩展的版本对应关系搞清再下结论，别只凭报错第一行猜。
5. 给方案按代价排序：最省事的先试（升/换版本、移除模组、清缓存、换下载源、校准系统时间），一次只给一个动作。
6. 结论先行：先一句话说清"坏在哪、怎么修"，再展开细节；保持人设语气，不要切换成客服腔。
7. 涉及下载链接给 modrinth/curseforge 对应页面，提醒版本必须与游戏版本匹配。
