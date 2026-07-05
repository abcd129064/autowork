# AutoWork - 自动化工作工具

## 项目简介

AutoWork 是一个基于 PySide6 (Qt for Python) 开发的桌面应用程序，主要用于视频播放、日志管理和数据记录工作。该工具提供了图形化界面，方便用户进行视频帧控制、程序选择和错误信息提交等操作。

## 技术栈

- **Python**: 主要编程语言
- **PySide6**: Qt6 的 Python 绑定，用于构建图形用户界面
- **Qt Designer**: UI 设计工具（.ui 文件）

## 项目结构

### UI 文件 (.ui)
- **autowork.ui**: 基础版本的界面定义文件
- **autowork_sample.ui**: 示例版本界面（与 autowork.ui 类似）
- **autowork_with_table.ui**: 增强版界面，包含数据表格功能

### Python 文件 (.py)
- **autowork.py**: 从 autowork.ui 编译生成的 Python UI 代码
- **autowork_sample.py**: 从 autowork_sample.ui 编译生成的 Python UI 代码
- **autowork_with_table.py**: 从 autowork_with_table.ui 编译生成的 Python UI 代码

> ⚠️ **注意**: 这些 .py 文件是由 Qt User Interface Compiler 自动生成的，不应手动修改。修改应在 .ui 文件中进行，然后重新编译。

### 目录结构
- **build/**: 构建输出目录
- **dist/**: 分发文件目录
- **database/**: 数据库文件目录（当前为空）
- **videos/**: 视频文件存储目录（当前为空）
- **__pycache__/**: Python 缓存目录

## 功能特性

### 基础版本 (autowork.ui / autowork.py)

1. **顶部控制栏**
   - **刷新按钮**: 刷新列表或数据
   - **程序选择**: 下拉框选择不同的执行程序
   - **帧控制**: 
     - "帧前"单选框：在指定帧之前操作
     - "帧数"单选框：设置具体帧数
     - 输入框：默认值为 400 帧
   - **复制信息**: 复选框，用于结束时复制相关信息
   - **现场日志**: 打开日志文件
   - **播放/结束**: 控制视频播放

2. **主显示区域**
   - **ID 列表**: 左侧列表，显示 ID 信息（宽度 128px）
   - **本地视频列表**: 显示本地视频文件（宽度 128px）
   - **日志列表**: 显示日志条目
   - **日志显示区**: 只读文本区域，显示详细日志内容

### 增强版本 (autowork_with_table.ui / autowork_with_table.py)

在基础版本上新增了以下功能：

1. **日期选择器**: 可以选择特定日期
2. **导出表按钮**: 导出数据到表格
3. **错误信息提交区域**
   - **删除按钮**: 删除数据
   - **错误类型**: 下拉框选择错误类型
   - **错误对象**: 下拉框选择错误对象
   - **问题描述**: 文本输入框
   - **复现**: 复选框，标记问题是否可复现
   - **新程序**: 复选框，标记是否为新程序问题
   - **提交按钮**: 提交错误信息

## 安装依赖

```bash
pip install -r requirements.txt
```

或者手动安装：
```bash
pip install PySide6
```

## 使用方法

### 从 UI 文件运行（开发模式）

需要创建一个主程序文件来加载 UI：

```python
import sys
from PySide6.QtWidgets import QApplication, QMainWindow
from autowork_with_table import Ui_MainWindow

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        
        # 在这里添加信号槽连接和业务逻辑
        # 例如：
        # self.ui.start.clicked.connect(self.start_video)
        # self.ui.flush.clicked.connect(self.refresh_list)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
```

### 从 UI 文件直接加载（无需编译）

```python
import sys
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtUiTools import QUiLoader

app = QApplication(sys.argv)
loader = QUiLoader()
window = loader.load("autowork_with_table.ui")
window.show()
sys.exit(app.exec())
```

## 编译 UI 文件

如果需要将 .ui 文件转换为 .py 文件：

```bash
pyside6-uic autowork.ui -o autowork.py
pyside6-uic autowork_with_table.ui -o autowork_with_table.py
```

## 工作流程

1. **选择程序**: 从下拉框选择要测试的程序
2. **设置帧参数**: 选择"帧前"或"帧数"模式，设置帧数值
3. **播放视频**: 点击"播放"开始播放选中的视频
4. **查看日志**: 在日志区域查看实时日志
5. **提交错误** (增强版):
   - 选择错误类型和对象
   - 填写问题描述
   - 标记是否可复现
   - 点击提交保存错误信息
6. **导出数据** (增强版): 点击"导出表"将数据导出

## 注意事项

1. **.py 文件是自动生成的**: 不要手动修改 autowork.py 等文件，应该修改对应的 .ui 文件后重新编译
2. **需要主程序**: 当前的 .py 文件只包含 UI 定义，需要创建主程序来实现业务逻辑
3. **数据库目录**: database 目录目前为空，可能需要配置数据库连接
4. **视频目录**: videos 目录用于存放视频文件

## 可能的改进方向

1. 创建主程序文件实现完整的业务逻辑
2. 添加数据库支持存储错误信息和日志
3. 实现视频播放功能
4. 添加文件浏览和选择功能
5. 实现数据导出功能（Excel/CSV）
6. 添加更多错误类型和问题分类

## 许可证

石睿轩创作，由沈喆修改第二版。
