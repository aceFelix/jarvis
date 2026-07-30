---
trigger: always_on
---
# Backend 开发规则


## 代码开发后注释

写完代码或修改完代码，无论是类还是方法，都必须为代码加上或更新注释，说明代码的功能和实现，注释作者为 aceFelix。
碰上核心实现代码或难懂的代码，必须用注释加以解释说明。

注释规范：
- 类/接口：顶部添加 Javadoc 描述类的职责和用途，包含 `@author aceFelix`
- 方法/函数：使用 Javadoc 描述参数、返回值及功能
- 复杂逻辑：在关键步骤前添加行内注释说明意图
- Controller：标注接口的请求路径、方法和业务含义
- Service：描述业务逻辑的处理流程
- Repository：说明数据访问的实体和查询目的

## 代码修改后验证

修改后端代码（Java/Spring Boot）后，**只需要验证代码能否编译通过**，不需要启动后端项目。

验证命令：

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\bitinn-dev\bitinn; mvn compile -q
```

- 编译通过（exit code 0）即表示修改无误
- 编译失败则根据错误信息修复后重新验证
- **禁止**在修改代码后自动执行 `mvn spring-boot:run` 启动项目
