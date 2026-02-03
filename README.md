# 狐蒂云商品识别助手 (Hudiyun Helper Scripts)

这是一个用于自动扫描狐蒂云 (szhdy.com) 有效商品页面的工具集。

## 功能特性

- **自动扫描**：自动遍历指定范围的 PID，识别有效的商品页面。
- **价格获取**：使用 Playwright 自动获取动态加载的商品价格。
- **结果可视化**：提供简单的 HTML 页面 (`index.html`) 查看和搜索扫描结果。
- **自动运行**：包含 GitHub Action，每天自动运行两次扫描并更新结果。
- **直达链接**：生成的 JSON 结果包含通过配置页直达购买的链接。

## 文件说明

- `狐蒂云商品识别.py`: 核心 Python 扫描脚本。
- `index.html`: 扫描结果查看器（双击打开即可）。
- `hudiyun_results.json`: 存放扫描结果的数据文件。
- `.github/workflows/daily_scan.yml`: GitHub Action 自动化配置。
- `狐蒂云商品识别.js`: (旧版) Tampermonkey 油猴脚本。

## 使用方法

### 本地运行

1.  **安装依赖**
    需要 Python 3.9+。
    ```bash
    # 创建并激活虚拟环境 (可选)
    python3 -m venv venv
    source venv/bin/activate  # macOS/Linux
    # venv\Scripts\activate   # Windows

    # 安装依赖
    pip install playwright
    playwright install chromium
    ```

2.  **运行脚本**
    ```bash
    python3 狐蒂云商品识别.py
    ```
    
    可选参数：
    - `--start`: 起始 PID (默认 1850)
    - `--end`: 结束 PID (默认 1900)
    
    示例：
    ```bash
    python3 狐蒂云商品识别.py --start 2000 --end 2050
    ```

3.  **查看结果**
    双击打开目录下的 `index.html` 文件，即可在浏览器中查看最新的扫描结果。支持按 PID 或标题搜索。

### 自动化部署 (GitHub Actions)

本项目已配置 GitHub Actions：
- 每天 **00:00** 和 **12:00** (UTC) 自动运行。
- 自动扫描 PID 1850-1900。
- 扫描结果会自动提交并更新到仓库中的 `hudiyun_results.json`。
- 您可以通过 GitHub Pages 部署本仓库，直接在线访问 `index.html` 查看结果。

## 注意事项

- 价格及商品有效性以商家官网实时数据为准。
- 脚本仅供学习交流使用，请勿用于非法用途。
