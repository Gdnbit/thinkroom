export const routeLoaders = {
  '/chat': () => import('@/pages/DecisionChatFlow'),
  '/knowledge': () => import('@/pages/KnowledgeBase'),
  '/review': () => import('@/pages/Review'),
}

export function preloadPrimaryRoute(path: string) {
  if (Object.prototype.hasOwnProperty.call(routeLoaders, path)) {
    void routeLoaders[path as keyof typeof routeLoaders]().catch(() => {
      // Navigation owns loading errors. A speculative request should stay silent.
    })
  }
}
