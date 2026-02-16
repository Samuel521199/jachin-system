/**
 * 打字机效果工具
 * 
 * 模拟流式输出，逐字显示文本
 */

export interface TypewriterOptions {
  speed?: number; // 每个字符的延迟（毫秒），默认 30ms
  onUpdate?: (text: string) => void; // 每次更新回调
  onComplete?: () => void; // 完成回调
}

/**
 * 打字机效果 - 逐字显示文本
 */
export async function typewriter(
  text: string,
  options: TypewriterOptions = {}
): Promise<void> {
  const { speed = 30, onUpdate, onComplete } = options;
  
  return new Promise((resolve) => {
    let currentIndex = 0;
    let displayedText = "";
    
    const interval = setInterval(() => {
      if (currentIndex >= text.length) {
        clearInterval(interval);
        onComplete?.();
        resolve();
        return;
      }
      
      // 添加下一个字符
      displayedText += text[currentIndex];
      currentIndex++;
      
      // 调用更新回调
      onUpdate?.(displayedText);
    }, speed);
  });
}

/**
 * 打字机效果 - 使用 requestAnimationFrame（更流畅）
 */
export function typewriterAnimation(
  text: string,
  options: TypewriterOptions = {}
): Promise<void> {
  const { speed = 30, onUpdate, onComplete } = options;
  
  return new Promise((resolve) => {
    let currentIndex = 0;
    let displayedText = "";
    let lastTime = performance.now();
    
    const animate = (currentTime: number) => {
      const elapsed = currentTime - lastTime;
      
      if (elapsed >= speed) {
        if (currentIndex < text.length) {
          displayedText += text[currentIndex];
          currentIndex++;
          onUpdate?.(displayedText);
          lastTime = currentTime;
        } else {
          onComplete?.();
          resolve();
          return;
        }
      }
      
      requestAnimationFrame(animate);
    };
    
    requestAnimationFrame(animate);
  });
}

/**
 * 快速打字机效果 - 按词显示（更快）
 */
export async function fastTypewriter(
  text: string,
  options: TypewriterOptions = {}
): Promise<void> {
  const { speed = 50, onUpdate, onComplete } = options;
  
  // 按词分割（保留标点）
  const words = text.match(/[\u4e00-\u9fa5]|[a-zA-Z]+|[0-9]+|[^\s\w\u4e00-\u9fa5]/g) || [];
  
  return new Promise((resolve) => {
    let currentIndex = 0;
    let displayedText = "";
    
    const interval = setInterval(() => {
      if (currentIndex >= words.length) {
        clearInterval(interval);
        onComplete?.();
        resolve();
        return;
      }
      
      displayedText += words[currentIndex];
      currentIndex++;
      
      onUpdate?.(displayedText);
    }, speed);
  });
}
