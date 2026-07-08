# 蜂鸟巢历史成品卡资产目录

本目录用于上传、暂存、整理和重新处理 `蜂鸟巢 / Hummingbird Hive` 的全部历史成品卡版本。

当前状态：

```text
资产目录不按版本号命名。
本目录统一收纳蜂鸟巢所有历史成品卡、对比图、参考图与后续重处理素材。
已完成第一轮文件名整理：UUID 文件已改为连续编号，语义参考图已移入 reference/。
```

---

## 去重结论

```text
已检查当前上传的 16 张 PNG 的 blob SHA。
未发现完全相同文件。
因此本轮不删除图片内容，只做目录归类与统一重命名。
```

说明：

```text
本轮去重为“文件级去重”。
后续还需要对图片画面做人工视觉筛选，判断哪些是构图重复、质量较低或只保留作反例。
```

---

## 当前文件结构

```text
assets/hummingbird-hive/
  README.md
  history/
    hummingbird-hive-card-01.png
    hummingbird-hive-card-02.png
    hummingbird-hive-card-03.png
    hummingbird-hive-card-04.png
    hummingbird-hive-card-05.png
    hummingbird-hive-card-06.png
    hummingbird-hive-card-07.png
    hummingbird-hive-card-08.png
    hummingbird-hive-card-09.png
    hummingbird-hive-card-10.png
    hummingbird-hive-card-11.png
    hummingbird-hive-card-12.png
    hummingbird-hive-card-13.png
    hummingbird-hive-card-14.png
  reference/
    hummingbird-hive-v6-1-full-reference.png
    hummingbird-hive-v6-1-layout-markup.png
```

---

## 上传与整理建议

后续继续上传蜂鸟巢历史成品卡时，建议临时放入：

```text
assets/hummingbird-hive/inbox/
```

再统一整理到：

```text
history/      原始历史成品卡与旧版本图
selected/     筛选后可参考版本
reference/    明确语义的参考图
frame/        卡框与插画窗口参考
illustration/ 插图参考或重做插图
layout-guide/ 版式区域标注
export/       合成导出图
```

---

## 处理原则

```text
1. 本目录用于蜂鸟巢全部历史成品卡版本，不按 v6.1 单独建目录。
2. 历史成品卡只作为参考，不直接作为最终印刷文件。
3. 卡框、插图、文字排版必须分层处理。
4. 技能文案、编号、区域信息与 Logo 必须人工排版。
5. 后续先筛选历史图，再决定是否重新提取卡框、重做插图或重新合成样卡。
```
