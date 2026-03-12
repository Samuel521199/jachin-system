/**
 * SDRendererButton - 按钮组件渲染器
 */

import React from "react";
import { motion } from "framer-motion";
import { cn } from "../../utils/cn";

interface SDRendererButtonProps {
  title: string;
  action_type?: string;
  action_id?: string;
  action_data?: Record<string, any>;
  style?: "default" | "positive" | "destructive";
  icon_url?: string;
  className?: string;
  onClick?: (data?: Record<string, any>) => void;
}

export const SDRendererButton: React.FC<SDRendererButtonProps> = ({
  title,
  action_type = "Action.Submit",
  action_id,
  action_data,
  style = "default",
  icon_url,
  className,
  onClick,
}) => {
  const styleClasses = {
    default: "bg-gray-700 hover:bg-gray-600",
    positive: "bg-green-600 hover:bg-green-700",
    destructive: "bg-red-600 hover:bg-red-700",
  };

  const handleClick = () => {
    if (onClick) {
      onClick(action_data);
    } else {
      console.log("Button clicked:", { action_id, action_data });
    }
  };

  return (
    <motion.button
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      whileHover={{ scale: 1.05 }}
      whileTap={{ scale: 0.95 }}
      onClick={handleClick}
      className={cn(
        "px-4 py-2 rounded-lg text-white transition-colors flex items-center gap-2",
        styleClasses[style],
        className
      )}
    >
      {icon_url && <img src={icon_url} alt="" className="w-4 h-4" />}
      {title}
    </motion.button>
  );
};
