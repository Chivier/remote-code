# 技能管理 (skill-manager.ts)

`skill-manager.ts` 实现了技能文件的同步机制，将共享的 CLAUDE.md 和 .claude/skills/ 文件复制到项目目录。

**源文件**：`daemon/src/skill-manager.ts`

## 职责

1. 在创建会话时将共享技能文件同步到项目目录
2. 列出共享技能和项目级技能
3. 保护已有的项目级技能文件不被覆盖

## 设计理念：Skillshare 模型

Remote Code 使用一种 "技能共享"（Skillshare）模型来管理 Claude 的技能文件：

1. **中央技能目录** — 由 Head Node 通过 SCP 同步到远程机器的 `~/.remote-code/skills/` 目录
2. **按项目分发** — 创建会话时，技能文件被复制到项目目录
3. **不覆盖原则** — 如果项目目录已有同名文件，不会被覆盖

这样每个项目可以有自己的定制技能，同时也可以通过中央目录共享通用技能。

## 类结构

```typescript
class SkillManager {
    private skillsSourceDir: string;  // 共享技能目录
    // 默认: ~/.remote-code/skills
}
```

## 关键方法

### syncToProject(projectPath: string)

将共享技能同步到指定的项目目录。返回同步和跳过的文件列表。

```typescript
syncToProject(projectPath: string): { synced: string[]; skipped: string[] }
```

**同步流程**：

1. 检查共享技能目录是否存在，不存在则直接返回空结果

2. **同步 CLAUDE.md**：
   ```
   ~/.remote-code/skills/CLAUDE.md → /project/CLAUDE.md
   ```
   仅在目标文件不存在时复制。

3. **同步 .claude/skills/ 目录**：
   ```
   ~/.remote-code/skills/.claude/skills/* → /project/.claude/skills/*
   ```
   递归复制，自动创建目标目录，已存在的文件不覆盖。

**返回示例**：
```typescript
{
    synced: ["CLAUDE.md", ".claude/skills/my-skill.md"],
    skipped: [".claude/skills/existing-skill.md (already exists)"]
}
```

### listSharedSkills() -> string[]

列出共享技能目录中的所有技能文件。

```typescript
listSharedSkills(): string[]
// 返回: ["CLAUDE.md", ".claude/skills/skill-a.md", ".claude/skills/skill-b.md"]
```

### listProjectSkills(projectPath: string) -> string[]

列出项目目录中的技能文件。

```typescript
listProjectSkills("/home/user/project"): string[]
// 返回: ["CLAUDE.md", ".claude/skills/project-specific.md"]
```

## 内部方法

### copyDirRecursive(sourceDir, targetDir, synced, skipped)

递归复制目录内容。对每个文件：
- 如果是目录，递归进入
- 如果是文件且目标不存在，复制
- 如果是文件且目标已存在，跳过（记录到 `skipped`）

### listFilesRecursive(dir, baseDir, result)

递归列出目录中的所有文件，生成相对路径。

## 文件结构

共享技能目录的预期结构：

```
~/.remote-code/skills/
├── CLAUDE.md                    # 全局 Claude 指令
└── .claude/
    └── skills/
        ├── coding-standards.md  # 编码规范
        ├── review-checklist.md  # 代码审查清单
        └── team-patterns.md    # 团队模式
```

同步后项目目录：

```
/home/user/project/
├── CLAUDE.md                    # 从共享目录复制（如果之前没有）
├── .claude/
│   └── skills/
│       ├── coding-standards.md  # 从共享目录复制
│       ├── review-checklist.md  # 从共享目录复制
│       └── project-api.md       # 项目原有的，未被覆盖
└── src/
    └── ...
```

## 同步链路

技能文件的完整同步路径：

```
本地 ./skills/ 目录
    │
    ▼ (Head Node: ssh_manager.sync_skills via SCP)
远程 ~/.remote-code/skills/
    │
    ▼ (Daemon: skill_manager.syncToProject via fs.copyFile)
远程 /project/.claude/skills/
```

1. Head Node 使用 SCP 将本地 `skills/` 目录同步到远程的 `~/.remote-code/skills/`
2. Daemon 在创建会话时将 `~/.remote-code/skills/` 中的文件复制到项目目录

这两步使用相同的 "不覆盖" 策略。

## 与其他模块的关系

- **server.ts** — 在 `handleCreateSession` 中调用 `syncToProject()`
- Head Node 的 **ssh_manager.py** — 通过 SCP 同步技能到远程的共享目录
