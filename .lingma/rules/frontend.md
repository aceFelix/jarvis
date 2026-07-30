---
trigger: always_on
---
# Frontend 开发规则


## 代码开发后注释

写完代码或修改完代码，必须为代码加上或更新注释，说明代码的功能和实现，注释作者为 aceFelix。
碰上核心实现代码或难懂的代码，必须用注释加以解释说明。

注释规范：
- Vue 组件：在 `<script setup>` 顶部添加组件功能说明的注释
- 方法/函数：使用 JSDoc 风格描述参数、返回值及功能
- 复杂逻辑：在关键步骤前添加行内注释说明意图
- Pinia Store：在 store 定义顶部说明状态管理的职责
- API 请求：在请求函数上方说明接口用途

## 代码修改后验证

修改前端代码（Vue 3 / Vite）后，**只需要验证代码能否编译通过**，不需要启动开发服务器。

验证命令：

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\bitinn-dev\bitinn-vue; npx vite build --mode development
```

- 编译通过（exit code 0）即表示修改无误
- 编译失败则根据错误信息修复后重新验证
- **禁止**在修改代码后自动执行 `npm run dev` 启动开发服务器