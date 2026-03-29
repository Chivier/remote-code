# 技能管理器（skill-manager.ts）

**文件：** `daemon/src/skill-manager.ts`

负责将 CLAUDE.md 和 `.claude/skills/` 文件从共享源目录同步到远程机器上的项目目录。

## 用途

- 在会话创建时将共享技能同步到项目目录
- 遵循"技能共享"模型，通过中央源提供通用技能
- 避免覆盖已有的项目特定技能

## 架构

技能在系统中经历两个阶段的流转：

1. **Head Node -> 远程机器**：Head Node 上的 SSHManager 通过 SCP 将技能从本地 `skills.shared_dir` 复制到远程机器的 `~/.codecast/skills`。
2. **远程技能目录 -> 项目**：守护进程上的 SkillManager 在创建会话时将 `~/.codecast/skills` 中的文件复制到特定项目目录。

## 类：SkillManager

```typescript
class SkillManager {
    private skillsSourceDir: string;
    // 默认：~/.codecast/skills
}
```

源目录默认为 `~/.codecast/skills`（基于 `HOME` 环境变量）。

## 关键方法

### `syncToProject(projectPath: string) -> { synced: string[], skipped: string[] }`

将技能从共享源同步到项目目录。在 `session.create` 期间由 `server.ts` 调用。

**行为：**

1. 如果源目录不存在，返回空结果（不报错）
2. **CLAUDE.md**：将 `CLAUDE.md` 从源目录复制到项目根目录，但**仅当项目中不存在该文件时**。已有的项目专属 `CLAUDE.md` 不会被覆盖。
3. **.claude/skills/**：创建目标目录结构并递归复制技能文件，**跳过目标中已存在的文件**。

**返回值：**
- `synced`：已复制的相对文件路径列表
- `skipped`：跳过的相对文件路径列表（附有"(already exists)"后缀）

### `listSharedSkills() -> string[]`

列出共享源目录中所有可用的技能。返回相对于源目录的文件路径。

### `listProjectSkills(projectPath: string) -> string[]`

列出特定项目目录中存在的所有技能。返回形如 `CLAUDE.md` 和 `.claude/skills/...` 的文件路径。

## 私有方法

### `copyDirRecursive(sourceDir, targetDir, synced, skipped)`

从源目录递归复制文件到目标目录。根据需要创建子目录。对每个文件：
- 如果目标不存在：复制文件，添加到 `synced`
- 如果目标已存在：跳过，添加到 `skipped`

### `listFilesRecursive(dir, baseDir, result)`

递归列出目录中的文件，存储相对于基础目录的路径。

## 不覆盖原则

SkillManager 永远不会覆盖已有文件。这是有意为之的设计决策：

- 项目可能有包含项目特定指令的 `CLAUDE.md`
- 共享技能提供一个项目可以定制的基准
- 初次同步后，项目文件优先级更高

## 示例

给定如下源目录结构：

```
~/.codecast/skills/
├── CLAUDE.md
└── .claude/
    └── skills/
        ├── coding-standards.md
        └── review-checklist.md
```

以及如下项目状态：

```
/home/user/project/
├── CLAUDE.md                    # 已存在——不会覆盖
└── .claude/
    └── skills/
        └── coding-standards.md  # 已存在——不会覆盖
```

结果：
- `synced`：`[".claude/skills/review-checklist.md"]`
- `skipped`：`["CLAUDE.md (already exists)", ".claude/skills/coding-standards.md (already exists)"]`

## 与其他模块的关系

- **server.ts** 创建单一的 SkillManager 实例，在 `session.create` 期间调用 `syncToProject()`
- Head Node 的 **ssh_manager.py** 负责在远程机器上填充技能源目录
