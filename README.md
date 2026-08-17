# 🐾 桌宠（DesktopPet）

一款绿色免安装的 Windows 桌面陪伴桌宠：透明无边框、始终置顶，支持摸头蹦跳动画、气泡对话、
大模型人格聊天与离线互动。**解压即用，无需安装 Python 或任何依赖。**

- 技术栈：Python 3.9+ / PySide6 / Pillow（打包后运行端零依赖）
- 所有路径均为相对路径，程序目录整体拷贝/移动即可使用

---

## ✨ 功能特性

| 模块 | 说明 |
| --- | --- |
| 形象 | 支持导入 JPG/PNG，自动抠图去背景（内置 flood-fill 算法，装了 rembg 可自动用 AI 抠图）；也支持直接拖图片到宠物身上 |
| AI 形象 | 上传任意图片 → 视觉模型分析角色特征 → 文生图生成**完整全身 Q 版立绘** → 自动抠图换装；失败自动降级为直接抠图 |
| 窗口 | 透明无边框、默认置顶；左键拖动位置、滚轮缩放大小、位置与大小自动记忆 |
| 动画 | 两套风格：**卡通弹性**（团子式 Q 弹压扁）与**人物动作**（呼吸摇摆/走路/挥手/鞠躬/摸头/蹦跳），按形象自动判断，也可手动切换 |
| 气泡 | 点击互动弹出文字气泡，逐字显示、自动消失、悬浮宠物四周不遮挡主体，样式全部可在配置中修改 |
| 对话 | 「人格 JSON + 大模型 API」架构；离线时自动降级到本地对话库，无网络也能互动 |
| 人格包 | 右键菜单一键导入/导出人格包（zip），人格包可携带形象图片，方便互相分享 |
| 打包 | 一键脚本打包为单文件夹绿色版 + zip 压缩包，解压双击 exe 即运行 |

---

## 🚀 快速开始

### 方式一：绿色版（推荐给普通用户）

1. 解压 `桌宠绿色版_v1.0.0.zip` 到任意目录（建议非系统盘，如 `D:\桌宠`）；
2. 双击 `DesktopPet.exe`；
3. 桌宠出现在屏幕右下角，开箱即用。

> 若被解压到无写权限的目录（如 Program Files），设置与形象会自动存到
> `%APPDATA%\DesktopPet\` 下，不影响使用。

### 方式二：源码运行（开发者）

```bat
:: 需要 Python 3.9+
python -m pip install -r requirements.txt
python main.py
```

自检模式（截图验证 + 核心逻辑断言，结束后自动退出）：

```bat
python main.py --selftest
```

---

## 🖱 使用说明

| 操作 | 效果 |
| --- | --- |
| 左键按住拖动 | 移动桌宠位置 |
| 滚轮 | 缩放大小（25% ~ 300%） |
| 左键单击头部 | 摸头动画 + 爱心 + 气泡台词，并打开对话面板 |
| 左键单击身体 | 蹦跳动画 + 气泡台词，并打开对话面板 |
| 右键 | 菜单：大小调整 / 置顶开关 / 打开对话面板 / 更换形象 / 导入·导出人格包 / 打开人格文件夹 / 退出 |
| 拖图片到桌宠上 | 自动抠图并更换形象 |
| 聊天框输入后 Enter | 发送消息（命中关键词优先彩蛋，否则走大模型，离线走本地库） |

在聊天中输入「我叫 小明」，桌宠会记住你的昵称并保存到配置。

---

## 🎨 形象替换方法

有三种方式：

1. **菜单导入**：右键 →「更换形象…」→ 选择 JPG/PNG，程序自动去背景并裁剪；
2. **拖拽导入**：把图片文件直接拖到桌宠身上；
3. **手动替换**：用任意工具（如 PS 抠图）处理好透明 PNG 后，覆盖 `assets/pet.png`（同目录结构），重启程序生效。

抠图参数在 `config/settings.json` 的 `matting` 节：

```json
"matting": {
  "method": "auto",     // auto=装了rembg优先AI抠图，否则内置算法；floodfill=强制内置算法
  "tolerance": 32       // 背景容差：抠不干净→调大；把主体抠掉了→调小
}
```

> 内置算法适合纯色/近似纯色背景的图；复杂背景建议 `pip install rembg`
> （首次运行会自动下载 AI 模型）或提前用专业工具抠好。

---

## ✨ AI 形象完善（可选，不配也能用）

上传的二次元人物图往往不是完整全身（半身、特写、裁剪），AI 模式会自动完善：

1. **视觉模型**分析图片 → 提取发色/发型/瞳色/服装/标志性配色/配饰等结构化特征；
2. 按特征构造提示词 → **文生图模型**生成「全身二头身 Q 版立绘」（约定纯浅蓝色背景）；
3. 内置抠图（浅蓝背景提示 + 边角校验回退）→ 保存为新形象，**自动切换「人物动作」动画风格**。

### 使用流程

- 拖图片到桌宠 / 右键「更换形象…」→ 弹窗三选一：**AI 生成 Q 版形象 / 仅抠图直接使用 / 取消**；
- 选择 AI 生成后，进度通过气泡显示（分析 → 绘制 → 抠图），全程后台执行不卡界面；
- 任一步失败都会弹窗说明原因，并自动降级为「仅抠图直接使用」；
- 未配置 AI 时弹窗会提示配置位置，「AI 生成」按钮置灰。

### 配置（config/settings.json，key 建议放 settings.local.json）

```json
"image_gen": {
  "enabled": true,
  "base_url": "https://api.siliconflow.cn/v1",   // 默认硅基流动
  "api_key": "sk-你的密钥",
  "model": "Kwai-Kolors/Kolors",
  "image_size": "768x1024",
  "guidance_scale": 7.5,
  "num_inference_steps": 30,
  "seed": -1,
  "timeout": 120
},
"vision": {
  "enabled": true,
  "base_url": "https://api.siliconflow.cn/v1",
  "api_key": "sk-你的密钥",
  "model": "Qwen/Qwen2.5-VL-72B-Instruct",
  "timeout": 60
}
```

支持的服务商（OpenAI 兼容 `/images/generations` 与多模态 `/chat/completions`）：

| 服务 | image_gen 示例 | vision 示例 |
| --- | --- | --- |
| 硅基流动（默认） | `base_url=https://api.siliconflow.cn/v1`，`model=Kwai-Kolors/Kolors` | `model=Qwen/Qwen2.5-VL-72B-Instruct` |
| OpenAI | `base_url=https://api.openai.com/v1`，`model=gpt-image-1`，`image_size=1024x1536` | `model=gpt-4o-mini` |
| 智谱 | `base_url=https://open.bigmodel.cn/api/paas/v4`，`model=cogview-3-flash` | `model=glm-4v-plus` |

程序按模型名自动切换请求格式（`gpt-image` 走 OpenAI 结构，其余走硅基流动结构），
并兼容 `images[0].url` / `data[0].b64_json` / `data[0].url` 三种响应。

> **费用与耗时**：每次 AI 生成调用 2 次 API（视觉分析 + 文生图，失败自动重试 1 次），
> 全程约 1 分钟。文生图返回的临时链接在请求内即时下载，不会过期。
> `image_gen.enabled=false` 或 `api_key` 留空即可完全关闭该功能。

---

## 🎬 动画风格

| 风格 | 适用形象 | 表现 |
| --- | --- | --- |
| 卡通弹性 cartoon | 团子类圆润形象 | Q 弹压扁、弹性回弹、大幅度摇摆 |
| 人物动作 humanoid | Q 版立绘人物 | 轻微呼吸、幅度收敛；支持**走路**（真实水平移动窗口，屏幕边缘自动停下，拖动即中断）、**挥手**（钟摆式摇摆）、**鞠躬**（绕脚踝前倾） |

- **自动判定**：`animation.style=auto` 时按图片比例判断——高/宽 ≥ 1.15 判为人物，否则卡通；
  AI 生成的形象会自动设为 humanoid；
- **手动切换**：右键 →「动画风格」→ 自动 / 卡通弹性 / 人物动作（立即生效并记忆）；
- 全部 11 种动作名：`pat / bounce / jump / spin / squish / dance / shake / happy / walk / wave / bow`。

---

## 🧬 人格配置文件修改教程

所有人格配置都在 `personalities/<人格名>/personality.json`，**改完重启生效**，无需改任何代码。

### 完整示例

```json
{
  "name": "团子",
  "version": "1.0.0",
  "author": "你自己",

  "personality": {
    "tone": "软萌、元气、治愈系",                       // 性格基调
    "speech_style": "短句、语气词多、句尾爱用～",          // 说话语气
    "catchphrases": ["团子在这里哟～"],                   // 常用口头禅
    "background": "一只由主人想象力捏出来的小团子"          // 角色背景
  },

  "memory": {                                          // 专属记忆库
    "草莓大福": {"fact": "团子最爱的食物", "trigger": true},
    "主人": {"fact": "摸头手法天下第一", "trigger": false}
  },

  "keyword_rules": [                                   // 关键词触发规则
    {"keywords": ["生日"], "replies": ["生日快乐！！🎂"],
     "action": "dance", "weight": 20}
  ],

  "easter_eggs": [                                     // 彩蛋台词（结构与上相同）
    {"keywords": ["彩蛋"], "replies": ["被发现啦！❤"], "action": "happy", "weight": 30}
  ],

  "offline_replies": {                                 // 离线对话库（按句子类别）
    "greeting": ["主人来啦～"],
    "question": ["团子想想哦……"],
    "default": ["嗯嗯？团子在听哦～"]
  }
}
```

### 字段说明

| 字段 | 作用 |
| --- | --- |
| `personality.*` | 注入大模型的角色设定：性格基调、语气风格、口头禅、背景故事 |
| `memory` | 专属记忆库。`trigger: true` 的词命中即触发回忆台词；`false` 只注入提示词。值也可写成 `{"fact": "...", "reply": "自定义台词", "action": "happy", "trigger": true}` |
| `keyword_rules` | 关键词触发规则：输入命中 `keywords` 任一子串即触发，多条命中取 `weight` 最高者；`action` 可选 `pat / bounce / jump / spin / squish / dance / shake / happy / walk / wave / bow` |
| `easter_eggs` | 彩蛋台词，机制与关键词规则一致（命中即彩蛋+动作） |
| `offline_replies` | 离线兜底库，支持类别：`greeting / bye / thanks / praise / comfort / question / pat / jump / idle / default` |

### 人格包分享

- **导出**：右键 →「导出人格包…」→ 生成 zip（含 personality.json + 当前形象图片）；
- **导入**：右键 →「导入人格包…」→ 选择 zip，人格与形象立即生效；
- 也可以直接把 `personalities/` 下的文件夹拷给别人。

---

## 🤖 接入大模型（可选，不配也能玩）

编辑 `config/settings.json` 的 `llm` 节（或新建 `settings.local.json` 只放这一节，更安全）：

```json
"llm": {
  "enabled": true,
  "base_url": "https://api.openai.com/v1",   // OpenAI 兼容接口地址
  "api_key": "sk-你的密钥",
  "model": "gpt-4o-mini",
  "timeout": 15
}
```

任何支持 OpenAI `POST {base_url}/chat/completions` 协议的服务都可以用：

| 服务 | base_url | 示例模型 |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Moonshot Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` |
| Ollama 本地 | `http://localhost:11434/v1` | `qwen2.5:7b` |

`api_key` 留空 = 始终离线模式。网络不通或接口报错时自动降级到离线对话库，聊天面板会显示「在线/离线」状态。

---

## 📦 打包步骤（开发者）

一键打包（需要本机有 Python 3.9+ 与网络，用于下载依赖）：

```bat
build.bat
```

或手动执行：

```bat
python -m pip install -r requirements.txt
pyinstaller desktop_pet.spec --noconfirm
```

产物：

- `dist/桌宠绿色版/` — 单文件夹绿色版（内含 DesktopPet.exe，全部依赖已内嵌）；
- `dist/桌宠绿色版_v1.0.0.zip` — 压缩包，发给接收方**解压双击 exe 即用**。

> 打包前如已通过菜单更换过形象，`assets/pet.png` 即当前形象，会一并打包。
> 未使用 UPX 压缩，避免杀毒软件误报。

---

## 📁 目录结构

```
desktop-pet/
├── main.py                    # 程序入口
├── build.bat                  # 一键打包脚本（开发者）
├── desktop_pet.spec           # PyInstaller 配置
├── requirements.txt
├── pet_app/                   # 核心代码
│   ├── controller.py          # 总控：组装与交互接线
│   ├── window.py              # 透明置顶窗口（拖动/缩放/右键菜单/走路平移）
│   ├── animations.py          # 动画控制器（双风格/待机/摸头/蹦跳/走路/挥手/鞠躬）
│   ├── bubble.py              # 文字气泡（逐字显示/自动消失/四向避让跟随）
│   ├── chat_panel.py          # 对话面板 + 后台请求线程
│   ├── character_ai.py        # AI 形象：视觉分析/文生图提示词/生成流水线
│   ├── dialogue.py            # 离线对话引擎（关键词/分类匹配）
│   ├── llm_client.py          # OpenAI 兼容 API 客户端（聊天+文生图，标准库实现）
│   ├── personality.py         # 人格包加载/导入/导出
│   ├── matting.py             # 自动抠图（flood-fill / rembg / 背景色提示）
│   ├── default_image.py       # 内置默认形象绘制
│   ├── config.py              # 全局配置读写
│   └── utils.py               # 路径/日志工具
├── personalities/             # 人格包目录（可任意增删）
│   └── default/personality.json
├── config/settings.json       # 全局配置（气泡样式/LLM/行为等）
├── assets/                    # 形象与图标
│   ├── pet.png                # 当前形象（替换/导入都会更新它）
│   └── pet.ico
└── scripts/build.py           # 打包脚本
```

**绿色版目录说明**：打包后的 `桌宠绿色版/` 中，出厂副本（assets、personalities、config、
README）位于 `_internal/` 内（只读）。你导入的新形象/人格、修改后的配置会保存在 exe 同级
的 `assets/`、`personalities/`、`config/` 目录中（首次变更时自动创建），程序优先读取这些
用户文件——所以「手动替换 assets/pet.png」在绿色版中同样成立。

---

## ❓ 常见问题排查（FAQ）

**Q1：双击 exe 没反应 / 一闪而过？**
查看日志 `config/logs/app.log`（绿色版在程序目录或 `%APPDATA%\DesktopPet\config\logs\`）。
常见原因：目录无写权限、杀毒软件拦截、系统缺少 VC++ 运行库（下载安装「Microsoft Visual C++ 2015-2022 Redistributable x64」）。

**Q2：桌宠窗口不透明 / 有黑边？**
桌面右键 → 显示设置，确认「透明效果」已开启；显卡驱动过旧也可能导致，尝试更新驱动。

**Q3：杀毒软件报毒？**
PyInstaller 打包的 exe 偶发误报（未加壳未混淆），添加信任即可；或改用源码方式运行。

**Q4：点击没反应 / 拖不动？**
气泡区域是鼠标穿透的，请点击宠物本体；若宠物被其他置顶窗口覆盖，可右键菜单关闭再打开「置顶显示」。

**Q5：气泡颜色/字体想改？**
编辑 `config/settings.json` 的 `bubble` 节（背景色、边框色、文字色、圆角、透明度、逐字速度、停留时长、最大宽度），重启生效。

**Q6：抠图效果不好？**
调整 `matting.tolerance`（详见上文）；复杂背景建议安装 rembg 或提前用专业工具抠成透明 PNG 再手动替换 `assets/pet.png`。

**Q7：大模型调用失败 / 一直显示离线？**
- `api_key` 为空 → 本来就是离线模式；
- 面板提示「HTTP 401」→ Key 无效，检查 `llm.api_key`；
- 提示「配置错误」→ 检查 `base_url` 是否可访问（企业网络可能需要代理）；
- 离线模式下所有交互依然可用（本地对话库）。

**Q8：想同时开两个桌宠？**
默认不允许（单实例保护，防止误开多个）。若确实想养两只，可修改 `main.py` 中 `QSharedMemory("DesktopPet_SingleInstance_v1")` 的键名后重新打包。

**Q9：宠物跑出屏幕外了？**
把它拖回来即可；位置保存后重启会校验是否在屏幕内，不在则回到主屏右下角。

**Q10：想恢复出厂设置？**
删除 `config/settings.json`（程序会自动重建默认配置）；删除 `assets/pet.png` 则恢复默认形象。

**Q11：AI 形象生成失败怎么办？**
查看日志 `config/logs/app.log`。常见原因：api_key 无效（提示 HTTP 4xx）、
网络不通/超时（提示 URLError）、余额不足（HTTP 429/402）。任一步失败都会自动降级为
直接抠图使用，不影响其他功能。

**Q12：不想用 AI 生成 / 怕误触扣费？**
把 `config/settings.json` 中 `image_gen.enabled` 与 `vision.enabled` 设为 `false`，
或保持 `api_key` 留空——导入弹窗中的「AI 生成」按钮会置灰，永远只走抠图流程。

**Q13：AI 生成的形象和手动抠图有什么区别？**
AI 生成：不完整的人物会被补全为全身 Q 版立绘，动画自动切换为「人物动作」风格；
手动抠图：图片什么样形象就是什么样（半身图仍是半身），风格按图片比例自动判定，
也可右键手动切换。两者可随时通过再次导入互相覆盖。

---

## 📄 许可

MIT License。人格配置文件、形象图片等用户资源归其创作者所有。
