---
name: "vue3-project-standards"
description: "Vue3项目目录结构规范及页面组件设计规范。在创建Vue3项目、重构代码、审查代码结构或需要规范指导时调用。"
---

# Vue3 项目目录结构规范及页面组件设计规范

## 1. 目录结构规范

### 1.1 标准目录结构

```
src/
├── api/                    # API 接口管理
│   ├── modules/           # 按业务模块划分的 API
│   │   ├── user.js
│   │   ├── chat.js
│   │   └── knowledge.js
│   └── index.js           # API 统一导出
├── assets/                # 静态资源
│   ├── images/           # 图片资源
│   ├── icons/            # 图标资源
│   └── styles/           # 全局样式
│       ├── variables.scss    # SCSS 变量
│       ├── mixins.scss       # SCSS 混入
│       └── global.scss       # 全局样式
├── components/            # 公共组件
│   ├── base/             # 基础组件（纯展示）
│   │   ├── Button/
│   │   ├── Input/
│   │   └── Modal/
│   ├── business/         # 业务组件（带业务逻辑）
│   │   ├── ConversationList/
│   │   ├── ChatMessage/
│   │   └── UserInfoCard/
│   └── common/           # 通用组件（布局、功能）
│       ├── CyberBackground/
│       ├── ThemeToggle/
│       └── Loading/
├── composables/          # 组合式函数
│   ├── useAuth.js
│   ├── useChat.js
│   └── usePermission.js
├── directives/           # 自定义指令
│   ├── permission.js
│   └── debounce.js
├── layouts/              # 布局组件
│   ├── DefaultLayout.vue
│   ├── AuthLayout.vue
│   └── AdminLayout.vue
├── router/               # 路由配置
│   ├── index.js          # 路由入口
│   ├── routes.js         # 路由定义
│   └── guards/           # 路由守卫
│       ├── authGuard.js
│       └── permissionGuard.js
├── stores/               # Pinia 状态管理
│   ├── index.js          # Store 入口
│   ├── modules/          # 按模块划分
│   │   ├── auth.js
│   │   ├── chat.js
│   │   ├── settings.js
│   │   └── user.js
│   └── plugins/          # Store 插件
│       └── persist.js
├── utils/                # 工具函数
│   ├── request.js        # HTTP 请求封装
│   ├── storage.js        # 本地存储封装
│   ├── validate.js       # 表单验证
│   ├── format.js         # 数据格式化
│   └── constants.js      # 常量定义
├── views/                # 页面视图
│   ├── landing/          # 落地页
│   │   └── LandingView.vue
│   ├── chat/             # 聊天模块
│   │   └── ChatView.vue
│   ├── admin/            # 管理后台
│   │   └── AdminDashboard.vue
│   └── auth/             # 认证模块
│       ├── LoginView.vue
│       └── RegisterView.vue
├── App.vue               # 根组件
└── main.js               # 应用入口
```

### 1.2 目录命名规范

- **小写命名**：所有目录名使用小写字母
- **单数优先**：除非明确表示复数概念，否则使用单数形式
  - ✅ `component/` ❌ `components/`
  - ✅ `api/` `utils/`（本身就是复数概念）
- **语义清晰**：目录名应准确表达其内容
- **避免嵌套过深**：最多 3 层嵌套

## 2. 组件设计规范

### 2.1 组件分类

#### 2.1.1 基础组件（Base Components）
- **特点**：纯展示，无业务逻辑
- **位置**：`src/components/base/`
- **命名**：大驼峰命名，如 `BaseButton.vue` 或 `Button.vue`
- **示例**：
  - Button、Input、Select、Modal、Toast

#### 2.1.2 业务组件（Business Components）
- **特点**：包含特定业务逻辑
- **位置**：`src/components/business/`
- **命名**：大驼峰命名，体现业务含义
- **示例**：
  - ConversationList、ChatMessage、UserInfoCard

#### 2.1.3 通用组件（Common Components）
- **特点**：跨页面复用的功能组件
- **位置**：`src/components/common/`
- **命名**：大驼峰命名
- **示例**：
  - CyberBackground、ThemeToggle、LoadingSpinner

#### 2.1.4 布局组件（Layout Components）
- **特点**：定义页面整体布局结构
- **位置**：`src/layouts/`
- **命名**：以 `Layout` 结尾
- **示例**：
  - DefaultLayout、AuthLayout、AdminLayout

### 2.2 组件文件结构

每个组件一个独立目录，包含以下文件：

```
ComponentName/
├── index.vue           # 组件主体（必须）
├── index.js            # 组件导出（可选）
├── README.md           # 组件文档（可选）
├── demo/
│   └── Demo.vue        # 组件示例（可选）
└── __tests__/
    └── ComponentName.spec.js  # 单元测试（推荐）
```

### 2.3 组件代码规范

#### 2.3.1 单文件组件结构

```vue
<template>
  <!-- 模板内容 -->
</template>

<script setup>
/**
 * 组件名称
 * @description 组件描述
 * @example 使用示例
 */

// 1. 导入（按类型分组，按字母排序）
// Vue 核心
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

// 组件
import ChildComponent from './ChildComponent.vue'
import CyberBackground from '@/components/common/CyberBackground.vue'

// 组合式函数
import { useAuth } from '@/composables/useAuth'
import { usePermission } from '@/composables/usePermission'

// Store
import { useUserStore } from '@/stores/modules/user'
import { useSettingsStore } from '@/stores/modules/settings'

// 工具函数
import { formatDate } from '@/utils/format'
import { validateEmail } from '@/utils/validate'

// API
import { getUserInfo, updateUser } from '@/api/modules/user'

// 常量
import { USER_STATUS } from '@/utils/constants'

// 2. 组件选项（如果有）
defineOptions({
  name: 'ComponentName',
  inheritAttrs: false
})

// 3. Props 定义
const props = defineProps({
  // 基础类型
  title: {
    type: String,
    required: true,
    default: ''
  },
  // 对象类型
  user: {
    type: Object,
    default: () => ({})
  },
  // 数组类型
  items: {
    type: Array,
    default: () => []
  },
  // 函数类型
  onSubmit: {
    type: Function,
    default: null
  },
  // 枚举类型
  type: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'primary', 'danger'].includes(value)
  }
})

// 4. Emits 定义
const emit = defineEmits([
  'update:modelValue',
  'submit',
  'cancel',
  'error'
])

// 5. 注入（如果有）
const injectedValue = inject('key', defaultValue)

// 6. Store 使用
const userStore = useUserStore()
const { userInfo, isLoggedIn } = storeToRefs(userStore)

// 7. Router 使用
const router = useRouter()
const route = useRoute()

// 8. 组合式函数使用
const { checkPermission } = usePermission()
const { logout } = useAuth()

// 9. 响应式数据
// ref 用于基础类型
const count = ref(0)
const isLoading = ref(false)
const errorMessage = ref('')

// reactive 用于对象类型
const form = reactive({
  username: '',
  email: '',
  password: ''
})

// 10. 计算属性
const displayTitle = computed(() => {
  return props.title || '默认标题'
})

const filteredItems = computed(() => {
  return props.items.filter(item => item.visible)
})

// 11. Watchers
watch(() => props.user, (newVal, oldVal) => {
  if (newVal?.id !== oldVal?.id) {
    loadUserData()
  }
}, { deep: true, immediate: true })

// 12. 方法定义
// 异步方法
async function loadUserData() {
  isLoading.value = true
  try {
    const { data } = await getUserInfo(props.user.id)
    userStore.setUserInfo(data)
  } catch (error) {
    errorMessage.value = error.message
    emit('error', error)
  } finally {
    isLoading.value = false
  }
}

// 同步方法
function handleSubmit() {
  if (!validateForm()) return
  
  emit('submit', form)
  
  if (props.onSubmit) {
    props.onSubmit(form)
  }
}

function validateForm() {
  if (!form.username) {
    errorMessage.value = '用户名不能为空'
    return false
  }
  if (!validateEmail(form.email)) {
    errorMessage.value = '邮箱格式不正确'
    return false
  }
  return true
}

// 13. 生命周期钩子
onMounted(() => {
  loadUserData()
})

onUnmounted(() => {
  // 清理工作
})

// 14. 暴露给父组件的方法（如果有）
defineExpose({
  reset,
  validate,
  submit: handleSubmit
})

function reset() {
  Object.assign(form, {
    username: '',
    email: '',
    password: ''
  })
}

function validate() {
  return validateForm()
}
</script>

<style scoped>
/* 组件样式 */
.component-container {
  /* 样式内容 */
}
</style>
```

#### 2.3.2 命名规范

**组件名**：
- 大驼峰命名（PascalCase）
- 多单词组合，避免单单词（防止与 HTML 元素冲突）
- ✅ `UserInfoCard` ❌ `User` `Card`

**文件名**：
- 与组件名一致
- 始终使用 `.vue` 扩展名
- ✅ `UserInfoCard.vue` ❌ `user-info-card.vue`

**Props 名**：
- 小驼峰命名（camelCase）
- 在模板中使用 kebab-case
- ✅ `userInfo` → `<user-info>`

**事件名**：
- 使用动词或动词短语
- ✅ `submit`、`cancel`、`update:modelValue`
- ❌ `click`、`doSomething`

**方法名**：
- 动词开头，小驼峰命名
- 事件处理：`handle` + 事件名
  - ✅ `handleSubmit`、`handleClick`、`handleUserSelect`
- 获取数据：`fetch` 或 `load` + 数据名
  - ✅ `fetchUserList`、`loadUserData`
- 判断方法：`is`、`has`、`can` 开头
  - ✅ `isValid`、`hasPermission`、`canEdit`

**变量名**：
- 布尔值：`is`、`has`、`show`、`can` 开头
  - ✅ `isLoading`、`hasError`、`showModal`
- 数组：复数形式
  - ✅ `users`、`items`、`messageList`
- 对象：单数形式
  - ✅ `user`、`form`、`config`

#### 2.3.3 Props 定义规范

```javascript
const props = defineProps({
  // 基础类型
  title: {
    type: String,
    required: true,
    default: ''
  },
  
  // 数值类型
  count: {
    type: Number,
    default: 0
  },
  
  // 布尔类型
  visible: {
    type: Boolean,
    default: false
  },
  
  // 数组类型 - 使用工厂函数返回默认值
  items: {
    type: Array,
    default: () => []
  },
  
  // 对象类型 - 使用工厂函数返回默认值
  config: {
    type: Object,
    default: () => ({})
  },
  
  // 函数类型
  onSubmit: {
    type: Function,
    default: null
  },
  
  // 枚举类型 - 使用 validator 验证
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['small', 'medium', 'large'].includes(value)
  },
  
  // 自定义类型
  user: {
    type: Object,
    default: () => ({
      id: '',
      name: '',
      avatar: ''
    }),
    validator: (value) => {
      return value.id && value.name
    }
  }
})
```

#### 2.3.4 样式规范

**Scoped 样式**：
- 始终使用 `scoped` 属性
- 根元素使用组件名作为类名

```vue
<style scoped>
.user-info-card {
  /* 组件根样式 */
}

.user-info-card__header {
  /* BEM 命名 */
}

.user-info-card__content {
  /* BEM 命名 */
}
</style>
```

**CSS 变量**：
- 使用 CSS 变量定义主题色
- 在 `:root` 或组件根元素定义

```css
.user-info-card {
  --card-bg: var(--bg-primary, #1a1a2e);
  --card-border: var(--border-color, rgba(139, 92, 246, 0.2));
  
  background: var(--card-bg);
  border: 1px solid var(--card-border);
}
```

## 3. 页面（Views）规范

### 3.1 页面组件特点

- 位于 `src/views/` 目录
- 一个页面对应一个路由
- 可以包含多个业务组件
- 负责页面级数据获取和状态管理

### 3.2 页面组件结构

```vue
<template>
  <div class="page-name">
    <!-- 科幻背景等全局效果 -->
    <CyberBackground />
    
    <!-- 页面布局 -->
    <div class="page-content">
      <!-- 侧边栏（如果有） -->
      <Sidebar />
      
      <!-- 主内容区 -->
      <main class="main-content">
        <!-- 页面头部 -->
        <PageHeader />
        
        <!-- 页面主体 -->
        <div class="page-body">
          <!-- 业务组件组合 -->
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
/**
 * 页面名称
 * @description 页面描述
 */

import { onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

// 布局组件
import CyberBackground from '@/components/common/CyberBackground.vue'
import Sidebar from '@/components/business/Sidebar.vue'
import PageHeader from '@/components/business/PageHeader.vue'

// Store
import { useUserStore } from '@/stores/modules/user'

// 组合式函数
import { usePermission } from '@/composables/usePermission'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { checkPermission } = usePermission()

// 页面级状态

// 页面初始化
onMounted(() => {
  // 权限检查
  if (!checkPermission('PAGE_ACCESS')) {
    router.push('/403')
    return
  }
  
  // 加载页面数据
  initPage()
})

async function initPage() {
  // 页面初始化逻辑
}
</script>

<style scoped>
.page-name {
  min-height: 100vh;
  position: relative;
}

.page-content {
  display: flex;
  position: relative;
  z-index: 1;
}

.main-content {
  flex: 1;
  padding: 24px;
}
</style>
```

### 3.3 页面命名规范

- 大驼峰命名
- 以 `View` 结尾
- ✅ `ChatView.vue`、`AdminDashboard.vue`
- ❌ `Chat.vue`、`Admin.vue`

## 4. 组合式函数（Composables）规范

### 4.1 命名规范

- 以 `use` 开头
- 小驼峰命名
- ✅ `useAuth`、`useChat`、`usePermission`

### 4.2 文件结构

```javascript
// src/composables/useFeature.js

import { ref, computed, onMounted, onUnmounted } from 'vue'

/**
 * 功能描述
 * @param {string} param1 - 参数1说明
 * @param {Object} options - 配置选项
 * @returns {Object} 返回值说明
 */
export function useFeature(param1, options = {}) {
  // 响应式状态
  const data = ref(null)
  const loading = ref(false)
  const error = ref(null)
  
  // 计算属性
  const isReady = computed(() => !loading.value && data.value !== null)
  
  // 方法
  async function fetchData() {
    loading.value = true
    error.value = null
    
    try {
      // 异步操作
      data.value = await api.fetch(param1)
    } catch (err) {
      error.value = err.message
    } finally {
      loading.value = false
    }
  }
  
  // 生命周期
  onMounted(() => {
    fetchData()
  })
  
  onUnmounted(() => {
    // 清理工作
  })
  
  // 返回值
  return {
    data,
    loading,
    error,
    isReady,
    fetchData
  }
}
```

## 5. Store 规范（Pinia）

### 5.1 目录结构

```
stores/
├── index.js              # Store 入口
├── modules/              # 按模块划分
│   ├── auth.js           # 认证模块
│   ├── user.js           # 用户模块
│   ├── chat.js           # 聊天模块
│   └── settings.js       # 设置模块
└── plugins/
    └── persist.js        # 持久化插件
```

### 5.2 Store 定义规范

```javascript
// src/stores/modules/user.js

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getUserInfo, updateUser } from '@/api/modules/user'

/**
 * 用户状态管理
 */
export const useUserStore = defineStore('user', () => {
  // ============ State ============
  const userInfo = ref(null)
  const token = ref(localStorage.getItem('token') || '')
  const loading = ref(false)
  
  // ============ Getters ============
  const isLoggedIn = computed(() => !!token.value)
  const userId = computed(() => userInfo.value?.id || '')
  const username = computed(() => userInfo.value?.username || '')
  
  // ============ Actions ============
  async function fetchUserInfo() {
    if (!token.value) return
    
    loading.value = true
    try {
      const { data } = await getUserInfo()
      userInfo.value = data
    } catch (error) {
      console.error('获取用户信息失败:', error)
      throw error
    } finally {
      loading.value = false
    }
  }
  
  function setToken(newToken) {
    token.value = newToken
    localStorage.setItem('token', newToken)
  }
  
  function clearUser() {
    userInfo.value = null
    token.value = ''
    localStorage.removeItem('token')
  }
  
  // ============ Return ============
  return {
    // State
    userInfo,
    token,
    loading,
    // Getters
    isLoggedIn,
    userId,
    username,
    // Actions
    fetchUserInfo,
    setToken,
    clearUser
  }
})
```

## 6. API 规范

### 6.1 目录结构

```
api/
├── index.js              # 请求实例配置
├── modules/              # 按业务模块划分
│   ├── user.js
│   ├── chat.js
│   ├── knowledge.js
│   └── auth.js
└── interceptors/         # 拦截器
    ├── request.js
    └── response.js
```

### 6.2 API 定义规范

```javascript
// src/api/modules/user.js

import request from '../index'

/**
 * 用户相关 API
 */

/**
 * 获取用户信息
 * @param {string} userId - 用户ID
 * @returns {Promise<Object>} 用户信息
 */
export function getUserInfo(userId) {
  return request.get(`/api/user/${userId}`)
}

/**
 * 更新用户信息
 * @param {string} userId - 用户ID
 * @param {Object} data - 用户数据
 * @returns {Promise<Object>} 更新后的用户信息
 */
export function updateUser(userId, data) {
  return request.put(`/api/user/${userId}`, data)
}

/**
 * 删除用户
 * @param {string} userId - 用户ID
 * @returns {Promise<void>}
 */
export function deleteUser(userId) {
  return request.delete(`/api/user/${userId}`)
}

/**
 * 获取用户列表
 * @param {Object} params - 查询参数
 * @param {number} params.page - 页码
 * @param {number} params.size - 每页数量
 * @param {string} params.keyword - 搜索关键词
 * @returns {Promise<Object>} 用户列表和分页信息
 */
export function getUserList(params) {
  return request.get('/api/user/list', { params })
}
```

## 7. 工具函数规范

### 7.1 目录结构

```
utils/
├── index.js              # 统一导出
├── request.js            # HTTP 请求封装
├── storage.js            # 本地存储封装
├── validate.js           # 表单验证
├── format.js             # 数据格式化
├── constants.js          # 常量定义
└── helpers/              # 辅助函数
    ├── date.js
    ├── string.js
    └── array.js
```

### 7.2 工具函数定义规范

```javascript
// src/utils/validate.js

/**
 * 验证邮箱格式
 * @param {string} email - 邮箱地址
 * @returns {boolean} 是否有效
 */
export function isValidEmail(email) {
  const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  return regex.test(email)
}

/**
 * 验证手机号格式
 * @param {string} phone - 手机号
 * @returns {boolean} 是否有效
 */
export function isValidPhone(phone) {
  const regex = /^1[3-9]\d{9}$/
  return regex.test(phone)
}

/**
 * 验证密码强度
 * @param {string} password - 密码
 * @returns {Object} 验证结果
 */
export function checkPasswordStrength(password) {
  const result = {
    valid: false,
    score: 0,
    message: ''
  }
  
  if (!password || password.length < 6) {
    result.message = '密码长度至少6位'
    return result
  }
  
  // 评分逻辑...
  
  return result
}
```

## 8. 最佳实践

### 8.1 组件通信

**Props Down, Events Up**：
```vue
<!-- 父组件 -->
<template>
  <ChildComponent 
    :data="parentData"
    @update="handleUpdate"
    @submit="handleSubmit"
  />
</template>

<!-- 子组件 -->
<script setup>
const props = defineProps(['data'])
const emit = defineEmits(['update', 'submit'])

function handleClick() {
  emit('update', newData)
}
</script>
```

**Provide/Inject**（跨层级通信）：
```javascript
// 祖先组件
provide('user', readonly(user))

// 后代组件
const user = inject('user')
```

### 8.2 性能优化

**懒加载组件**：
```javascript
const AsyncComponent = defineAsyncComponent(() => 
  import('./HeavyComponent.vue')
)
```

**虚拟列表**（大数据量）：
```javascript
import { useVirtualList } from '@vueuse/core'

const { list, containerProps, wrapperProps } = useVirtualList(
  hugeList,
  { itemHeight: 50 }
)
```

**防抖和节流**：
```javascript
import { debounce, throttle } from 'lodash-es'

const debouncedSearch = debounce((query) => {
  search(query)
}, 300)

const throttledScroll = throttle(() => {
  handleScroll()
}, 100)
```

### 8.3 错误处理

**全局错误处理**：
```javascript
// main.js
app.config.errorHandler = (err, vm, info) => {
  console.error('Vue Error:', err)
  console.error('Component:', vm)
  console.error('Info:', info)
  
  // 上报错误
  reportError(err)
}
```

**组件级错误处理**：
```vue
<script setup>
import { onErrorCaptured } from 'vue'

onErrorCaptured((err, instance, info) => {
  console.error('组件错误:', err)
  // 阻止错误向上传播
  return false
})
</script>
```

### 8.4 代码复用

**优先使用组合式函数**：
```javascript
// 复用逻辑
function useCounter() {
  const count = ref(0)
  const increment = () => count.value++
  return { count, increment }
}

// 在多个组件中使用
const { count, increment } = useCounter()
```

**渲染函数复用**：
```javascript
// 复用渲染逻辑
function useRenderItem(props) {
  return (item) => h('div', { class: 'item' }, item.name)
}
```

## 9. 文件组织检查清单

创建新功能时，检查以下清单：

- [ ] 组件放在正确的目录（base/business/common）
- [ ] 组件名使用大驼峰命名
- [ ] Props 有完整的类型定义和默认值
- [ ] 事件名使用动词开头
- [ ] 样式使用 scoped
- [ ] 方法名使用动词开头
- [ ] 异步方法使用 async/await
- [ ] 有适当的错误处理
- [ ] 有必要的注释
- [ ] 代码通过 ESLint 检查

## 10. 示例项目结构

```
my-vue3-project/
├── public/                    # 静态资源
│   ├── favicon.ico
│   └── images/
├── src/
│   ├── api/
│   │   ├── index.js
│   │   └── modules/
│   ├── assets/
│   │   ├── images/
│   │   └── styles/
│   ├── components/
│   │   ├── base/
│   │   ├── business/
│   │   └── common/
│   ├── composables/
│   ├── directives/
│   ├── layouts/
│   ├── router/
│   ├── stores/
│   ├── utils/
│   ├── views/
│   ├── App.vue
│   └── main.js
├── .env                       # 环境变量
├── .env.development
├── .env.production
├── .eslintrc.js              # ESLint 配置
├── .prettierrc               # Prettier 配置
├── index.html
├── package.json
├── README.md
└── vite.config.js            # Vite 配置
```

---

**总结**：遵循以上规范可以让 Vue3 项目结构清晰、代码可维护、团队协作高效。
