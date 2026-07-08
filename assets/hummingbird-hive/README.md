# 蜂鸟巢历史成品卡资产目录

本目录用于上传、暂存、整理和重新处理 `蜂鸟巢 / Hummingbird Hive` 的全部历史成品卡版本、无文字插图和合成样卡。

当前状态：

```text
资产目录不按版本号命名。
已完成第一轮文件名整理：UUID 文件已改为连续编号，语义参考图已移入 reference/。
蜂鸟巢无文字插图候选 01 已入库。
蜂鸟巢卡面编号调整为 N-007 / DN-D1。
下一步进入中文版样卡 v1 合成准备，样卡版本记录为 CN-v1.0。
```

---

## 去重结论

```text
已检查当前上传的 16 张 PNG 的 blob SHA。
未发现完全相同文件。
因此本轮不删除图片内容，只做目录归类与统一重命名。
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
  illustration/
    README.md
    hummingbird-hive-illustration-01.png
  composite/
    README.md
```

---

## 当前插图资产

```text
assets/hummingbird-hive/illustration/hummingbird-hive-illustration-01.png
```

用途：

```text
蜂鸟巢无文字插图候选 01。
用于后续合成中文版样卡 v1。
插图本身不包含卡名、技能、编号、区域信息和 Logo。
```

---

## 当前合成目标

中文版样卡 v1：

```text
蜂鸟巢
无人机域

查看节点牌库顶1张，可置顶或置底。

N-007 / DN-D1
归零者 / ZERO ACCESS
```

版本记录：

```text
卡面编号：N-007 / DN-D1
语言版本：中文版
样卡版本：CN-v1.0
插图版本：hummingbird-hive-illustration-01.png
计划导出：hummingbird-hive-cn-card-v1.png
```

说明：

```text
“控制此节点”作为权限节点技能规则写入规则说明，不再作为每张权限节点卡的固定前缀。
版本号进入资产文件名和审核记录，不作为正式规则文本。
```

计划输出：

```text
assets/hummingbird-hive/composite/hummingbird-hive-cn-card-v1.png
```

---

## 处理原则

```text
1. 历史成品卡只作为参考，不直接作为最终印刷文件。
2. 卡框、插图、文字排版必须分层处理。
3. 技能文案、编号、区域信息与 Logo 必须人工排版。
4. 后续先合成中文版样卡 v1，再根据审核结果决定是否制作英文版。
5. 每次导出样卡都必须记录卡面编号、语言版本、样卡版本、插图版本和卡框版本。
```
