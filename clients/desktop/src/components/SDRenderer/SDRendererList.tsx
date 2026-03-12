/**
 * SDRendererList - 列表组件渲染器
 */

import React from "react";
import { motion } from "framer-motion";
import { cn } from "../../utils/cn";

interface SDRendererListProps {
  title?: string;
  items: Array<Record<string, any>>;
  item_template?: Record<string, any>;
  show_index?: boolean;
  max_items?: number;
  className?: string;
}

export const SDRendererList: React.FC<SDRendererListProps> = ({
  title,
  items,
  item_template,
  show_index = false,
  max_items,
  className,
}) => {
  const displayItems = max_items ? items.slice(0, max_items) : items;

  const renderItem = (item: Record<string, any>, index: number) => {
    if (item_template) {
      // 使用模板渲染（TODO: 实现模板渲染逻辑）
      return (
        <div key={index} className="p-2 bg-gray-800/50 rounded mb-2">
          {JSON.stringify(item)}
        </div>
      );
    }

    // 默认渲染：显示所有键值对
    return (
      <motion.div
        key={index}
        initial={{ opacity: 0, x: -10 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: index * 0.05 }}
        className="p-3 bg-gray-800/50 rounded-lg mb-2 border border-purple-500/20 hover:border-purple-500/40 transition-colors"
      >
        {show_index && (
          <span className="text-purple-400 mr-2">#{index + 1}</span>
        )}
        <div className="space-y-1">
          {Object.entries(item).map(([key, value]) => (
            <div key={key} className="flex justify-between text-sm">
              <span className="text-gray-400">{key}:</span>
              <span className="text-white">{String(value)}</span>
            </div>
          ))}
        </div>
      </motion.div>
    );
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={cn("sdui-list", className)}
    >
      {title && (
        <h3 className="text-sm font-semibold text-purple-300 mb-3">{title}</h3>
      )}
      <div className="space-y-2">
        {displayItems.map((item, index) => renderItem(item, index))}
      </div>
      {max_items && items.length > max_items && (
        <div className="text-xs text-gray-500 mt-2">
          显示前 {max_items} 项，共 {items.length} 项
        </div>
      )}
    </motion.div>
  );
};
