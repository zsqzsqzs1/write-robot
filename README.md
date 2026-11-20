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
| **可复现** | 66 项单元测试覆盖几何、排版、表格、轨迹、G-code 与模拟器 |

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

### 三种运行方式，任选其一

**A. 双击 `wr.bat`（最省事）**

在项目目录里双击 `wr.bat`，会弹出中文交互菜单：

```
  write-robot · 写字机器人上位机
  --------------------------------------------
  1) 预览轨迹      生成 HTML 动画，看笔尖怎么走（无需硬件）
  2) 生成 G-code   输出 .nc 文件，可导入 GRBL 发送器
  3) 连接机器书写  需要先接好硬件
  4) 列出串口
  5) 查看机器配置
  0) 退出
```

选 1 输入文字和字号即可生成预览，程序会一直等你在窗口里按回车才关闭。

**B. 命令行（跨平台，可脚本化）**

```bash
writerobot preview "你好世界" -o preview.html --font-size 60 --align center --valign center
```

若提示找不到 `writerobot` 命令，说明 Scripts 目录不在 PATH 里，
改用等价的模块调用即可，功能完全一样：

```bash
python -m writerobot.cli preview "你好世界" -o preview.html
```

**C. 作为库调用**

```python
from writerobot import MachineConfig, WriteRequest, build_job, render_preview

machine = MachineConfig(kinematics="corexy", width=200, height=200)
job = build_job(WriteRequest(text="你好世界", machine=machine))
open("preview.html", "w", encoding="utf-8").write(render_preview(job))
```

### 写表格

```bash
writerobot table --file rows.json -o out/table.html --width 240 --height 180
```

`rows.json` 是一个二维数组：

```json
[["我是一名小学生", "我是一名中学生"],
 ["我是一名高中生", "我是一名大学生"],
 ["我是工人", "我是老师"]]
```

也可以直接在命令行用 `|` 分隔单元格：

```bash
writerobot table "我是一名小学生|我是一名中学生" "我是工人|我是老师"
```

表格线会先画完，再逐格写字 —— 和真实写字机器人一样。
字号与内边距都会自动缩放以适应书写区域。

### 生成的文件在哪

所有产物统一输出到项目目录下的 **`out/`**：

| 文件 | 内容 | 怎么来的 |
|---|---|---|
| `out/preview.html` | 轨迹预览动画（双击即可用浏览器打开） | `preview` / 菜单选 1 |
| `out/output.nc` | G-code 文件（可导入 GRBL 发送器） | `gcode` / 菜单选 2 |
| `out/greeting.html` | 示例脚本生成的预览 | `python examples/write_greeting.py` |
| `out/greeting.nc` | 示例脚本生成的 G-code | 同上 |
| `out/table.html` | 表格轨迹预览 | `table` / 菜单选 1 |

用 `-o` 可以指定任意路径，程序会自动创建缺失的目录：

```bash
writerobot preview "你好" -o D:/tmp/我的字.html
```

命令行会打印文件的完整路径，方便直接定位。

### 先看看效果（不需要任何硬件）

```bash
writerobot preview "你好世界" -o preview.html --font-size 60 --align center --valign center
```

用浏览器打开 `preview.html`：深色是落笔绘制的笔画，浅蓝虚线是抬笔空走的路径，
橙色圆点是笔尖位置。点「播放」按真实速度回放。

想直接让浏览器弹出，加 `--open`：

```bash
writerobot preview "你好世界" --open
```

### 生成 G-code

```bash
writerobot gcode "你好世界" -o out.nc --machine examples/machine_corexy_servo.json
```

### 写到纸上

```bash
writerobot ports                              # 先看看串口在哪
writerobot run "你好世界" --port COM3 --machine examples/machine_corexy_servo.json
```

> **Windows 用户注意**：命令行直接敲中文可能因控制台代码页（GBK）出现乱码，
> 此时先执行 `chcp 65001` 切换到 UTF-8。

---

## 命令一览

```
writerobot menu              交互菜单（双击 wr.bat 时进入）
writerobot table             把表格写出来（表格线 + 单元格文字）
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
| `--open` | 预览生成后自动用浏览器打开 |
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

### 表格线为什么不走平滑管线

表格框线是**几何直线**，不能当文字笔画处理。Catmull-Rom 经过矩形的四个角点时
会剧烈过冲，把直角甩成一个巨大的圆角团块（实测过）。

因此 `plan_trajectory` 增加了独立的 `geometry` 参数：几何线条原样绘制，
不经过平滑、圆角和重采样，并且总是先于文字绘制。

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
│   ├── table.py        表格排版：列宽行高计算、表格线、文字嵌入单元格
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
├── wr.bat              Windows 一键启动脚本
├── tests/              66 项单元测试
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
首次下载后会缓存，之后离线可用。写纯英文时**完全不发起网络请求**——
ASCII 字符直接查内置拉丁字体。

**Q：提示「找不到 writerobot 命令」？**
Windows 上 pip 的 Scripts 目录可能不在 PATH。用 `python -m writerobot.cli` 等价替代，
或直接用仓库里的 `wr.bat`。

**Q：双击 wr.bat 窗口一闪就没了 / 什么都没发生？**
双击会进入中文交互菜单，选 0 退出时程序会等你按回车才关闭。
如果想看命令行帮助，用 `wr.bat help`（会 `pause` 停住）。
另外双击时若提示「python 不是内部或外部命令」，说明 Python 没在 PATH 里，
请改用完整路径调用，或重新安装时勾选 Add to PATH。

**Q：Windows 下脚本会卡住不动？**
早期版本把启动脚本命名为 `writerobot.bat`，而脚本内部又调用 `writerobot`，
CMD 会优先在当前目录找到这个 bat 文件，导致无限递归卡死。现在脚本名为 `wr.bat`，
且内部固定用 `python -m writerobot.cli` 调用，不会再出现这个问题。

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
