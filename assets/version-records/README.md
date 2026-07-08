# 归零者｜全卡版本记录目录

> 文件定位：本目录用于记录《归零者 / ZERO ACCESS》所有卡牌的版本记录。
>
> 原则：每一张正式卡都必须能通过唯一卡号定位到版本记录，并通过版本记录复现同一张完整卡。

---

## 一、总原则

```text
所有卡都要记录。
每张卡只有一个唯一卡号。
版本记录归属到唯一卡号下面。
版本号不拼进卡号。
文件名可以包含卡号信息，但文件名不是第二套卡号。
```

---

## 二、版本记录必须包含

每张卡的版本记录至少包含：

```text
卡号
中文卡名
英文卡名
卡牌类型
语言版本
样卡版本
插图版本
卡框版本
导出版本
卡面正式信息
输入资产路径
输出资产路径
审核状态
复现要求
```

---

## 三、当前完成状态

```text
12 张权限节点：版本记录已全部建立。
23 张唯一执行者：版本记录已全部建立。
5 张唯一越权模块：版本记录已全部建立。
蜂鸟巢：已建立详细复现记录。
```

说明：

```text
当前版本记录多为“记录入口 + 卡面草案 + 待补资产路径”。
当插图、卡框和导出图确认后，应回填具体资产路径和审核结论。
```

---

## 四、当前目录结构

```text
assets/version-records/
  README.md
  nodes/
    README.md
    N-001_AD-A1.md
    N-002_AD-A2.md
    N-003_AD-A3.md
    N-004_MD-M1.md
    N-005_MD-M2.md
    N-006_MD-M3.md
    N-007_DN-D1.md
    N-008_DN-D2.md
    N-009_DN-D3.md
    N-010_CD-C1.md
    N-011_CD-C2.md
    N-012_CD-C3.md
  operators/
    README.md
    R-001_trace-runner.md
    ...
    R-023_red-alert-root.md
  override-gear/
    README.md
    G-001_containment-sandbox.md
    ...
    G-005_forced-rollback.md
```

说明：

```text
文件名将斜杠替换为下划线，便于文件系统保存。
文件名不是第二套卡号。
真实卡号以记录文件内的“唯一卡号”为准。
```

---

## 五、蜂鸟巢当前详细记录

蜂鸟巢当前详细记录位置：

```text
assets/hummingbird-hive/version-records/N-007_DN-D1.md
```

全卡目录入口：

```text
assets/version-records/nodes/N-007_DN-D1.md
```
