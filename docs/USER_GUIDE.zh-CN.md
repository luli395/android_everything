# Android Everything 使用说明

本文档介绍如何在 Windows 上安装、连接设备、建立文件索引，以及使用 Android Everything 搜索和管理 Android 设备中的文件。

英文版本请参阅 [English User Guide](USER_GUIDE.md)。

## 1. 使用前准备

### 1.1 系统要求

- Windows 10 或更高版本
- Python 3.8 或更高版本，并包含 Tkinter
- Android SDK Platform Tools（主要使用其中的 `adb`）
- 一台已开启 USB 调试的 Android 设备
- 一条支持数据传输的 USB 线

本项目目前只使用 Python 标准库，不需要执行 `pip install`。

### 1.2 安装 Python

从 [Python 官网](https://www.python.org/downloads/windows/) 下载并安装 Python。安装时建议勾选 **Add Python to PATH**。

在 PowerShell 中确认安装结果：

```powershell
python --version
python -c "import tkinter; print('Tkinter OK')"
```

两个命令都正常输出后即可继续。

### 1.3 安装 ADB

从 [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) 下载 Windows 版本并解压。

可选择以下任一配置方式：

#### 方式一：将 ADB 加入 PATH

把解压得到的 `platform-tools` 目录加入 Windows 的 `PATH` 环境变量，然后重新打开 PowerShell，执行：

```powershell
adb version
```

#### 方式二：仅为当前 PowerShell 指定路径

```powershell
$env:ANDROID_EVERYTHING_ADB = "C:\path\to\platform-tools\adb.exe"
```

如果采用此方式，需要从同一个 PowerShell 窗口启动程序。也可以将该环境变量添加到 Windows 用户环境变量中，使其长期生效。

## 2. 开启 Android USB 调试

不同品牌手机的菜单名称可能略有区别，通常按以下步骤操作：

1. 打开手机的 **设置 > 关于手机**。
2. 连续点击 **版本号** 或 **软件版本** 约 7 次，开启开发者选项。
3. 返回设置，进入 **系统 > 开发者选项**。
4. 开启 **USB 调试**。
5. 使用 USB 线连接电脑，并将 USB 用途设为 **文件传输**（若手机提供该选项）。
6. 手机上出现“是否允许 USB 调试”时，确认电脑指纹并点击 **允许**。

在 PowerShell 中检查连接：

```powershell
adb devices
```

正常情况下会看到类似结果：

```text
List of devices attached
XXXXXXXXXXXX    device
```

常见设备状态：

| 状态 | 含义与处理方式 |
| --- | --- |
| `device` | 已连接并授权，可以正常使用 |
| `unauthorized` | 手机尚未授权；解锁手机并接受 USB 调试提示 |
| `offline` | 连接异常；重新插拔 USB 线或执行 `adb kill-server`、`adb start-server` |
| 没有设备 | 检查数据线、USB 模式、手机驱动和 USB 调试设置 |

## 3. 获取并启动程序

```powershell
git clone https://github.com/luli395/android_everything.git
cd android_everything
python main.py
```

程序启动后，顶部区域包含搜索框、文件类型筛选器、设备选择器、**Refresh** 和 **Index** 按钮；底部显示当前状态、索引进度和文件数量。

## 4. 建立文件索引

首次连接设备后，需要先建立索引：

1. 确保手机处于解锁状态并已授权 USB 调试。
2. 点击右上角 **Refresh**，刷新设备列表。
3. 在 **Device** 下拉框中选择目标设备。
4. 点击 **Index**。
5. 等待底部进度完成，状态栏会显示已索引的文件数量。

索引过程中按钮会变为 **Stop**，可以点击它请求停止本次索引。

程序会自动检测内部存储、SD 卡及部分常见外部存储路径。索引只记录文件元数据，包括文件名、设备路径、大小和修改时间，不会把文件内容整体复制到电脑。

索引结果保存在项目目录下自动生成的 `files.db` 中。重新点击 **Index** 会清除该设备的旧索引并重新扫描。

> 注意：某些 Android 系统目录会受到系统权限限制，普通 ADB 会话只能索引当前用户可访问的文件。程序还会跳过 `/Android/data` 和 `.thumbnails` 等路径。

## 5. 搜索与筛选

### 5.1 搜索文件

在顶部搜索框输入文件名或路径中的关键词，结果会在短暂延迟后自动更新，也可以按 Enter 立即搜索。

搜索采用前缀匹配。例如：

- 输入 `photo` 可匹配以 `photo` 开头的词
- 输入 `report 2026` 会搜索同时包含相应词前缀的文件名或路径
- 清空搜索框会显示当前设备索引中的文件

单次最多显示 10,000 条结果。

### 5.2 按文件类型筛选

使用搜索框右侧的 **Type** 下拉框，可以筛选当前索引中数量较多的文件扩展名，例如 JPG、MP4 或 PDF。选择 **All** 取消类型筛选。

### 5.3 排序

点击结果列表中的列标题可按以下字段排序：

- Name：文件名
- Path：设备路径
- Size：文件大小
- Modified：修改时间
- Type：扩展名

再次点击同一列标题可切换升序和降序。

## 6. 文件操作

### 6.1 下载并打开

双击一条搜索结果，程序会先将文件下载到系统临时目录，再使用 Windows 默认应用打开。

临时目录通常为：

```text
%TEMP%\android_everything
```

### 6.2 保存到电脑

1. 选中一条或多条结果。可使用 Ctrl 或 Shift 进行多选。
2. 右键并选择 **Pull to PC**。
3. 单个文件时选择保存文件名；多个文件时选择目标文件夹。

同名文件保存到同一文件夹时可能互相覆盖，请提前整理目标目录。

### 6.3 在资源管理器中显示

右键文件并选择 **Show in Explorer**。程序会把第一个选中的文件下载到临时目录，然后在 Windows 资源管理器中定位该文件。

### 6.4 复制设备路径

选中一个或多个文件，右键选择 **Copy Path**。所选文件在 Android 设备上的完整路径会复制到剪贴板，多条路径以换行分隔。

### 6.5 从设备删除文件

1. 选中一个或多个文件。
2. 右键选择 **Delete**。
3. 仔细核对确认对话框后再确认删除。

删除操作针对 Android 设备中的原文件，而不仅仅是本地索引。删除成功后，对应记录也会从索引中移除。此操作不可撤销，重要文件应先使用 **Pull to PC** 备份。

## 7. 数据与隐私

- `files.db` 存储设备序列号以及已索引文件的名称和路径，应视为本地隐私数据。
- 从手机下载的文件及临时目录中的文件不会上传到 GitHub。
- 项目的 `.gitignore` 已排除 `files.db`、`phone/`、`downloads/` 和 Python 缓存，但在分享日志或截图前仍应检查其中是否包含个人信息。
- 如需清除索引，请先关闭程序，再删除项目目录下的 `files.db`。下次启动时会自动创建空数据库。
- 如需清除双击打开或“在资源管理器中显示”产生的临时文件，请删除 `%TEMP%\android_everything` 目录。

## 8. 常见问题

### 提示 “ADB was not found”

确认 `adb version` 可以在当前 PowerShell 中执行，或者正确设置：

```powershell
$env:ANDROID_EVERYTHING_ADB = "C:\path\to\platform-tools\adb.exe"
python main.py
```

### 程序显示 “No devices found”

依次检查：

1. `adb devices` 是否能看到设备。
2. 手机是否已经解锁并接受调试授权。
3. USB 线是否支持数据传输。
4. 是否安装了手机厂商提供的 Windows USB 驱动。
5. 点击程序中的 **Refresh**。

仍未恢复时可重启 ADB：

```powershell
adb kill-server
adb start-server
adb devices
```

### 索引数量少于手机文件管理器显示的数量

Android 的分区存储和应用沙箱会限制普通 ADB 对部分目录的访问。此外，当前版本主动跳过部分系统或缩略图目录。因此索引数量可能少于手机文件管理器的统计值。

### 搜索不到刚复制到手机的文件

当前版本不会实时监听设备文件变化。点击 **Index** 重新建立索引后再搜索。

### 文件大小或修改时间显示不完整

不同 Android 厂商的 `ls` 输出格式可能存在差异。如果无法解析相应字段，文件仍可被索引，但大小可能显示为 `0 B`，修改时间可能显示为 `-`。

### 双击文件后没有打开

确认电脑上安装了支持该格式的应用。也可以右键选择 **Pull to PC**，保存后自行打开。

## 9. 安全退出

等待正在进行的下载或索引完成后关闭窗口，然后再拔出 USB 线。程序的索引数据库会保留，下一次连接同一设备时可以直接搜索已有索引；设备文件发生变化后应重新建立索引。
