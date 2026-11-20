# write-robot · 写字机器人上位机

把文字变成会动的笔，再变成机器能执行的 G-code。

一套纯 Python 实现的写字机器��控制软件：**文字 → 笔画 → 排版 → 轨迹 → G-code → 串口下发**，
中间附带一个不需要任何硬件的浏览器动画模拟器，写之前就能看到笔尖怎么走。

```
你好  →  汉字笔画中线  →  排版到纸面  →  抬落笔轨迹  →  G-code  →  GRBL  →  步进电机
        (hanzi-writer-data)   (em 盒)      (CoreXY/直角)  (115200)
```

---

## 目录

- [特点](#特点)
- [快速开始](#快速开始)
- [命令一览](#命令一览)
- [机器配置](#机器配置)
- [接硬件](#接硬件)
- [算法说明](#算法说明)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [致谢与参考](#致谢与参考)

---

## 特点

| | |
|---|---|
| **真笔顺** | 汉字按标准笔顺书写，笔画数据取自 `hanzi-writer-data` 的中线（median），不是描边轮廓 |
| **零硬件可验证** | 自带 HTML 动画模拟器，按真实进给速度回放笔尖轨迹，还能拖动进度条逐帧检查 |
| **CoreXY 支持** | 固件解算与上位机解算两种路径都支持，公式与 GRBL `planner.c` 中的 `COREXY` 分支一致 |
| **抬落笔可配** | Z 轴升降（`G0 Z`）或 RC 舵机（`M3 S`），对应 `grbl-servo` 固件的 PWM 语义 |
| **纯 Python** | 核心功能零依赖；只有串口下发需要 `pyserial` |
| **可复现** | 51 项单元测试覆盖几何、排版、轨迹、G-code 与模拟器 |

---

## 快速开始

### 安装

```bash
git clone https://github.com/zsqzsqzs1/write-robot.git
cd write-robot
pip install -e .            # 核心功能
pip install -e ".[serial]"  # 需要串口下发时
```

Python 3.9+，无需编译。

### 先看看效果（不需要任何硬件）

```bash
writerobot preview "你好世界" -o preview.html --font-size 60 --align center --valign center
```

用浏览器打开 `preview.html`：深色是落笔绘制的笔画，浅蓝虚线是抬笔空走的路径，
橙色圆点是笔尖位置。点「播放」按真实速度回放。

### 生成 G-code

```bash
writerobot gcode "你好世界" -o out.nc --machine examples/machine_corexy_servo.json
```

### 写到纸上

```bash
writerobot ports                              # 先看看串口在哪
writerobot run "你好世界" --port COM3 --machine examples/machine_corexy_servo.json
```

---

## 命令一览

```
writerobot preview <文字>    生成轨迹预览（HTML 动画 / SVG），无需硬件
writerobot gcode   <文字>    生成 G-code 文件
writerobot run     <文字>    连接机器并直接书写
writerobot ports             列出可用串口
writerobot config            查看当前机器配置（JSON）
writerobot init-config       生成一份机器配置模板
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--font-size` | 字号，em 盒高度（mm）。汉字实际墨迹约为字号的 0.78 |
| `--align` / `--valign` | `left`/`center`/`right`，`top`/`center`/`bottom` |
| `--wrap` | 自动换行宽度（mm） |
| `--margin` | 四周留边（mm） |
| `--no-auto-fit` | 不自动缩小字号以适应区域 |
| `--corner-radius` | 拐角圆滑半径（mm），0 = 保留尖角 |
| `--sample-step` | 弧长重采样步长（mm） |
| `--no-smooth` | 不做样条平滑 |
| `--machine` | 机器配置文件 |
| `--pen-mode` | `z`（Z 轴）或 `servo`（舵机） |
| `--demo` / `--speed` | 预览加速播放 |
| `--offline` | 离线模式，只用本地缓存的汉字数据 |

---

## 机器配置

用 `writerobot init-config` 生成模板，或参考 `examples/` 下的两份示例。

```json
{
  "name": "corexy-200-servo",
  "kinematics": "corexy",
  "width": 200.0,
  "height": 200.0,
  "origin": "front-left",
  "draw_feed": 3000.0,
  "travel_feed": 9000.0,
  "corexy_host_solve": false,
  "pen": {
    "mode": "servo",
    "servo_down": 0,
    "servo_up": 1000
  }
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `kinematics` | `cartesian`（直角坐标）或 `corexy` |
| `width` / `height` | 可书写区域尺寸（mm），应与机械行程一致 |
| `origin` | 原点所在角：`front-left` / `front-right` / `back-left` / `back-right` |
| `draw_feed` | 落笔绘制速度（mm/min） |
| `travel_feed` | 抬笔空走速度（mm/min） |
| `corexy_host_solve` | `false` = 固件解算（GRBL 开 `COREXY` 宏）；`true` = 上位机解算 |
| `pen.mode` | `z` 或 `servo` |
| `pen.z_up` / `pen.z_down` | Z 轴抬笔 / 落笔坐标（mm） |
| `pen.servo_up` / `pen.servo_down` | 舵机抬笔 / 落笔的 S 值（0~1000） |

### CoreXY 两种模式怎么选

```jsonc
// 固件解算：GRBL 的 config.h 里 #define COREXY
{ "kinematics": "corexy", "corexy_host_solve": false }

// 上位机解算：固件是原版 GRBL，不认识 CoreXY
{ "kinematics": "corexy", "corexy_host_solve": true }
```

上位机解算用的公式与 GRBL `planner.c` 中 `COREXY` 分支一致：

```
ΔLA = ΔX + ΔY        （A 皮带轴）
ΔLB = ΔX - ΔY        （B 皮带轴）
```

> **绝大多数 CoreXY 写字机用第一种即可。** 只有当你的固件没开 `COREXY` 宏、
> 电机直接接 X/Y 轴时，才需要用第二种。

---

## 接硬件

### 1. 固件

| 需求 | 固件 |
|---|---|
| CoreXY + 舵机抬笔 | [grbl-servo](https://github.com/cprezzi/grbl-servo) + `config.h` 里 `#define COREXY` |
| 直角坐标 + Z 轴抬笔 | 原版 [GRBL 1.1](https://github.com/gnea/grbl) |

`grbl-servo` 的关键配置：

```c
#define VARIABLE_SPINDLE      // 把主轴 PWM 引脚当舵机信号用
#define COREXY                // CoreXY 运动学
#define SPINDLE_PWM_MIN_VALUE 0      // 抬笔位置
#define SPINDLE_PWM_MAX_VALUE 1000   // 落笔位置
```

### 2. 舵机方向

`grbl-servo` 里 S 值与脉宽成正比：**S=0 是最小脉宽，S=1000 是最大脉宽**。
哪个是抬笔、哪个是落笔，取决于舵机的安装方向。

装反了就互换配置里的两个值：

```json
{ "pen": { "servo_down": 1000, "servo_up": 0 } }
```

本项目因此把抬笔和落笔分别用 `M3 S<servo_up>` / `M3 S<servo_down>` 表达，
而不是假设「M5 = 抬笔」——不同固件对 M5 的处理并不一致。

### 3. 标定

```bash
writerobot run "" --port COM3 --dry-run   # 只看 G-code，不发送
```

上电后先用低速试写单个字：

```bash
writerobot run "一" --port COM3 --draw-feed 800 --travel-feed 1500
```

确认笔尖不划纸、笔画不缺不断，再调速度。

### 4. 参数速查（GRBL `$$`）

| 参数 | 含义 | 建议 |
|---|---|---|
| `$100` / `$101` | X / Y 每毫米步数 | 按实际传动比标定 |
| `$110` / `$111` | X / Y 最大速度 | 空走速度的实际上限 |
| `$120` / `$121` | X / Y 加速度 | 太大易丢步 |
| `$30` | 主轴 PWM 最大值 | 舵机模式设为 1000 |

---

## 算法说明

### 汉字笔画数据

取自 [`chanind/hanzi-writer-data`](https://github.com/chanind/hanzi-writer-data)（MIT）。
使用其中的 `medians` 字段——每一笔的**中心线**，正是笔尖要走的路径。

坐标变换依据 Hanzi Writer 源码中的官方定义：

```javascript
const [b, $] = [{x: 0, y: -124}, {x: 1024, y: 900}]
```

即墨迹框为 `x ∈ [0, 1024]`、`y ∈ [-124, 900]`（原始数据 Y 轴向下）。
**若误用 `0..1024` 归一化，汉字会整体上移约 12% 字高。** 本项目已按官方定义处理。

首次使用某字符时按需下载并缓存到 `~/.cache/write-robot/hanzi/`，
之后完全离线可用（`--offline`）。

### 为什么用向心参数化而不是等分

汉字中线点的分布极不均匀。实测「我」的第二笔：

```
段长: [41.4, 26.3, 445.3, 70.0]     ← 中间一段跨了 445px（1024 画布的 43%）
```

长横往往只用两三个点表示。等分参数的 Catmull-Rom 在长短段交界处会算出
巨大的切向量，导致曲线鼓包、笔画发毛。

本项目采用**向心参数化**（centripetal，α=0.5），节点间距按 `|Δp|^0.5` 分配，
可以有效抑制过冲与尖点。参考 Yuksel et al., *On the Parameterization of
Catmull-Rom Curves* (2011)。`tests/test_geometry.py` 里有对应的回归测试。

### 轨迹规划流程

```
笔画中线
  → 向心 Catmull-Rom 平滑（采样密度按弦长分配）
  → 拐角圆滑（二次贝塞尔，半径默认 0.6mm）
  → Douglas-Peucker 抽稀
  → 弧长等距重采样（默认 0.5mm）
  → 抬落笔规划
```

### 拐角圆滑

真实笔尖有直径，直角转弯会划出墨点。本项目在每个拐角处用二次贝塞尔曲线
过渡，保留转折方向但去掉尖角。转角小于 8°（近乎共线）或大于 175°（折返）时跳过。

### 空走合并

相邻笔画首尾相接时（距离 < 0.05mm）不抬笔，直接续画，省一次抬落笔。

---

## 项目结构

```
write-robot/
├── writerobot/
│   ├── geometry.py     几何工具：向心 Catmull-Rom、重采样、抽稀、包围盒
│   ├── fonts.py        内置单线拉丁字体（原创，A-Z / 0-9 / 标点）
│   ├── glyphs.py       字形来源：汉字笔画数据 + 拉丁字体，带磁盘缓存
│   ├── layout.py       排版：em 盒模型、多行、自动换行、自动缩放、对齐
│   ├── machine.py      机器配置：运动学、坐标系、抬落笔方式
│   ├── trajectory.py   轨迹规划：平滑、圆角、抬落笔、空走合并
│   ├── gcode.py        G-code 生成
│   ├── simulator.py    SVG 静态图 + HTML 交互动画
│   ├── serial_link.py  GRBL 串口通信（握手、状态查询、逐行流控）
│   ├── pipeline.py     高层流水线
│   └── cli.py          命令行入口
├── tests/              51 项单元测试
├── examples/           示例配置与示例脚本
└── docs/
```

---

## 常见问题

**Q：写出来的字是歪的 / 上下颠倒？**
检查 `origin` 配置。`front-left` 表示机器原点（左下）在纸的左下角；
若你的机器原点在前右方，改成 `front-right`。

**Q：CoreXY 机器动方向不对？**
先确认固件有没有开 `COREXY` 宏。开了就用 `corexy_host_solve: false`；
没开就用 `true`。若方向反了，把 `origin` 换到对角即可。

**Q：笔画断了 / 墨点很重？**
多半是拐角问题。调小 `--corner-radius`（如 0.3），或调大 `--sample-step`（如 0.8）
减少转角密度。另外检查机械间隙和笔尖是否磨损。

**Q：字迹太细？**
预览里的线宽只是示意，实际粗细由笔尖决定。写字机器人用的是真实墨水笔，
线宽不可调；如果需要粗笔画，请改用激光雕刻模式。

**Q：下载汉字数据很慢？**
数据源默认走 jsdelivr CDN（实测 ~1.2s/字符），失败时自动降级到 GitHub Raw。
首次下载后会缓存，之后离线可用。

**Q：支持的字符有哪些？**
`hanzi-writer-data` 收录了约 9000 个常用汉字，加上一套内置的拉丁字母、
数字与常用标点。不支持的字符会被跳过并在输出里提示。

---

## 致谢与参考

本项目的实现参考了以下开源项目，在此致谢：

- **[hanzi-writer-data](https://github.com/chanind/hanzi-writer-data)**（MIT，777⭐）
  —— 汉字笔画中线数据来源，本项目的核心依赖。
- **[Lxiangrui/Xbot](https://github.com/Lxiangrui/Xbot)**（61⭐）—— 复刻稚晖君的开源写字机器人，
  其 GRBL 移植版的 `planner.c` 提供了 CoreXY 运动学公式的权威参考。
- **[HogwartsRico/X-Bot](https://github.com/HogwartsRico/X-Bot)**（132⭐）——
  超迷你写字机器人，CoreXY 结构，软件电路结构全开源。
- **[wwptrdo/Laser_Writing_Machine](https://github.com/wwptrdo/Laser_Writing_Machine)**（64⭐）
  —— G-code 驱动的写字/雕刻机参考设计。
- **[mindcont/WriteRobot](https://github.com/mindcont/WriteRobot)** —— 写字机器人上位机，
  「上位机负责图像处理、生成运动轨迹」的职责划分思路来自这里。
- **[gnea/grbl](https://github.com/gnea/grbl)**（GPLv3）—— G-code 协议与 CoreXY 宏。
- **[cprezzi/grbl-servo](https://github.com/cprezzi/grbl-servo)** —— 舵机抬笔固件。

## License

MIT，见 [LICENSE](LICENSE)。汉字数据版权归 `hanzi-writer-data` 作者所有（MIT）。
