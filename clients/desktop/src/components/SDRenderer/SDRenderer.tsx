/**
 * SDRenderer - Server-Driven UI 渲染引擎
 * 
 * 递归组件，接收 SDUI JSON 数据并映射到 React 组件
 * 支持 Adaptive Cards 标准组件和 Jachin 扩展组件
 */

import React from "react";
import { motion } from "framer-motion";
import { SDRendererChart } from "./SDRendererChart";
import { SDRendererProgressBar } from "./SDRendererProgressBar";
import { SDRendererList } from "./SDRendererList";
import { SDRendererButton } from "./SDRendererButton";
import { SDRendererInput } from "./SDRendererInput";
import { cn } from "../../utils/cn";

export interface SDUIElement {
  type: string;
  [key: string]: any;
}

interface SDRendererProps {
  element: SDUIElement;
  className?: string;
  onSubmit?: (data: any) => void; // 用于处理 Action.Submit
}

export const SDRenderer: React.FC<SDRendererProps> = ({ element, className, onSubmit }) => {
  if (!element || !element.type) {
    return null;
  }

  const { type, ...props } = element;

  // 处理 Adaptive Cards 标准组件
  switch (type) {
    case "AdaptiveCard":
      return (
        <div className={cn("sdui-card", className)}>
          {props.body?.map((item: SDUIElement, index: number) => (
            <SDRenderer key={index} element={item} onSubmit={onSubmit} />
          ))}
          {props.actions && (
            <div className="flex gap-2 mt-4">
              {props.actions.map((action: SDUIElement, index: number) => (
                <SDRenderer key={index} element={action} onSubmit={onSubmit} />
              ))}
            </div>
          )}
        </div>
      );

    case "TextBlock":
      return (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn(
            "sdui-textblock",
            props.size === "large" && "text-lg",
            props.size === "medium" && "text-base",
            props.size === "small" && "text-sm",
            props.weight === "bolder" && "font-bold",
            props.color === "accent" && "text-purple-400",
            props.color === "warning" && "text-yellow-400",
            props.color === "attention" && "text-red-400",
            props.spacing === "small" && "mb-2",
            props.spacing === "medium" && "mb-4",
            props.spacing === "large" && "mb-6",
            className
          )}
        >
          {props.text}
        </motion.div>
      );

    case "Container":
      return (
        <div className={cn("sdui-container", className)}>
          {props.items?.map((item: SDUIElement, index: number) => (
            <SDRenderer key={index} element={item} onSubmit={onSubmit} />
          ))}
        </div>
      );

    case "ColumnSet":
      return (
        <div className={cn("flex gap-4", className)}>
          {props.columns?.map((column: SDUIElement, index: number) => (
            <div key={index} className="flex-1">
              {column.items?.map((item: SDUIElement, itemIndex: number) => (
                <SDRenderer key={itemIndex} element={item} onSubmit={onSubmit} />
              ))}
            </div>
          ))}
        </div>
      );

    case "Image":
      return (
        <motion.img
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          src={props.url}
          alt={props.alt_text || ""}
          className={cn("sdui-image", className)}
        />
      );

    // Jachin 扩展组件
    case "SDUI.Chart":
      return <SDRendererChart chart_type={props.chart_type ?? "line"} data={props.data ?? []} {...props} className={className} />;

    case "SDUI.ProgressBar":
      return <SDRendererProgressBar value={props.value ?? 0} {...props} className={className} />;

    case "SDUI.List":
      return <SDRendererList items={props.items ?? []} {...props} className={className} />;

    case "SDUI.Button":
      return <SDRendererButton title={props.title ?? ""} {...props} className={className} />;

    // Input 类型
    case "Input.Text":
    case "Input.Number":
    case "Input.Toggle":
    case "Input.ChoiceSet":
      return <SDRendererInput type={type as any} id={props.id ?? `Input.${type}.${Math.random().toString(36).slice(2)}`} {...props} className={className} />;

    // Action 类型
    case "Action.Submit":
      return (
        <button
          className={cn(
            "px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 text-white transition-colors",
            "focus:outline-none focus:ring-2 focus:ring-purple-500/50",
            className
          )}
          onClick={() => {
            // 收集表单数据（如果有输入框）
            const formData: Record<string, any> = {};
            if (props.data) {
              Object.assign(formData, props.data);
            }
            
            // 收集所有输入框的值
            const inputs = document.querySelectorAll('[id^="Input."]');
            inputs.forEach((input) => {
              const element = input as HTMLInputElement | HTMLTextAreaElement;
              if (element.id && element.value !== undefined) {
                formData[element.id] = element.value;
              }
            });
            
            if (onSubmit) {
              onSubmit(formData);
            } else {
              console.log("Submit action:", formData);
              // 如果没有提供 onSubmit，可以发送到后端
              // TODO: 实现默认提交逻辑
            }
          }}
        >
          {props.title}
        </button>
      );

    case "Action.OpenUrl":
      return (
        <button
          className={cn(
            "px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors",
            className
          )}
          onClick={() => {
            if (props.url) {
              window.open(props.url, "_blank");
            }
          }}
        >
          {props.title}
        </button>
      );

    default:
      console.warn(`Unknown SDUI element type: ${type}`, element);
      return (
        <div className={cn("text-yellow-400 text-sm", className)}>
          [Unknown: {type}]
        </div>
      );
  }
};
