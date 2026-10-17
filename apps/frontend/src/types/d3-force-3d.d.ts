// d3-force-3d 第三方包未提供 .d.ts；这里挂一个最低声明
// 本项目仅在 KnowledgeBase.tsx 内通过动态 import 调用，不依赖具体签名
declare module 'd3-force-3d' {
  const content: any
  export = content
}
