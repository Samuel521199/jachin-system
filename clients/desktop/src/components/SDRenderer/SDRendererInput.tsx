/**
 * SDRendererInput - SDUI 输入组件渲染器
 * 
 * 支持多种输入类型：
 * - Input.Text: 文本输入
 * - Input.Number: 数字输入
 * - Input.Toggle: 开关
 * - Input.ChoiceSet: 选择集（单选/多选）
 */

import React, { useState } from "react";
import { motion } from "framer-motion";
import { cn } from "../../utils/cn";

export interface SDRendererInputProps {
  type: "Input.Text" | "Input.Number" | "Input.Toggle" | "Input.ChoiceSet";
  id: string;
  placeholder?: string;
  value?: string | number | boolean;
  is_multiline?: boolean;
  max_length?: number;
  style?: string; // "text", "email", "tel", "url" for Input.Text
  min?: number; // for Input.Number
  max?: number; // for Input.Number
  title?: string; // for Input.Toggle
  value_on?: string; // for Input.Toggle
  value_off?: string; // for Input.Toggle
  choices?: Array<{ title: string; value: string }>; // for Input.ChoiceSet
  is_multiselect?: boolean; // for Input.ChoiceSet
  className?: string;
  onChange?: (value: any) => void;
}

export const SDRendererInput: React.FC<SDRendererInputProps> = ({
  type,
  id,
  placeholder,
  value,
  is_multiline,
  max_length,
  style,
  min,
  max,
  title,
  value_on = "true",
  value_off = "false",
  choices = [],
  is_multiselect = false,
  className,
  onChange,
}) => {
  const [inputValue, setInputValue] = useState(value);

  const handleChange = (newValue: any) => {
    setInputValue(newValue);
    if (onChange) {
      onChange(newValue);
    }
  };

  switch (type) {
    case "Input.Text":
      return (
        <motion.div
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn("sdui-input", className)}
        >
          {is_multiline ? (
            <textarea
              id={id}
              placeholder={placeholder}
              value={inputValue as string || ""}
              maxLength={max_length}
              onChange={(e) => handleChange(e.target.value)}
              className={cn(
                "w-full px-3 py-2 bg-gray-800/50 border border-purple-500/30 rounded-lg",
                "text-white placeholder-gray-500 focus:outline-none focus:border-purple-500",
                "focus:ring-2 focus:ring-purple-500/20 transition-all",
                className
              )}
              rows={4}
            />
          ) : (
            <input
              id={id}
              type={style || "text"}
              placeholder={placeholder}
              value={inputValue as string || ""}
              maxLength={max_length}
              onChange={(e) => handleChange(e.target.value)}
              className={cn(
                "w-full px-3 py-2 bg-gray-800/50 border border-purple-500/30 rounded-lg",
                "text-white placeholder-gray-500 focus:outline-none focus:border-purple-500",
                "focus:ring-2 focus:ring-purple-500/20 transition-all",
                className
              )}
            />
          )}
        </motion.div>
      );

    case "Input.Number":
      return (
        <motion.div
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn("sdui-input", className)}
        >
          <input
            id={id}
            type="number"
            placeholder={placeholder}
            value={inputValue as number || ""}
            min={min}
            max={max}
            onChange={(e) => handleChange(parseFloat(e.target.value) || 0)}
            className={cn(
              "w-full px-3 py-2 bg-gray-800/50 border border-purple-500/30 rounded-lg",
              "text-white placeholder-gray-500 focus:outline-none focus:border-purple-500",
              "focus:ring-2 focus:ring-purple-500/20 transition-all",
              className
            )}
          />
        </motion.div>
      );

    case "Input.Toggle":
      const isChecked = inputValue === value_on || inputValue === true;
      return (
        <motion.div
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn("sdui-input flex items-center gap-3", className)}
        >
          <label className="flex items-center cursor-pointer">
            <input
              id={id}
              type="checkbox"
              checked={isChecked}
              onChange={(e) => handleChange(e.target.checked ? value_on : value_off)}
              className="sr-only"
            />
            <div
              className={cn(
                "relative w-11 h-6 rounded-full transition-colors",
                isChecked ? "bg-purple-600" : "bg-gray-700"
              )}
            >
              <div
                className={cn(
                  "absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform",
                  isChecked && "transform translate-x-5"
                )}
              />
            </div>
            {title && (
              <span className="ml-3 text-sm text-gray-300">{title}</span>
            )}
          </label>
        </motion.div>
      );

    case "Input.ChoiceSet":
      const [selectedValues, setSelectedValues] = useState<string[]>(
        Array.isArray(inputValue) ? inputValue as string[] : inputValue ? [inputValue as string] : []
      );

      const handleChoiceChange = (choiceValue: string) => {
        let newValues: string[];
        if (is_multiselect) {
          newValues = selectedValues.includes(choiceValue)
            ? selectedValues.filter(v => v !== choiceValue)
            : [...selectedValues, choiceValue];
        } else {
          newValues = [choiceValue];
        }
        setSelectedValues(newValues);
        handleChange(is_multiselect ? newValues : newValues[0]);
      };

      return (
        <motion.div
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn("sdui-input space-y-2", className)}
        >
          {choices.map((choice, index) => {
            const isSelected = selectedValues.includes(choice.value);
            return (
              <label
                key={index}
                className={cn(
                  "flex items-center gap-2 p-2 rounded-lg cursor-pointer transition-colors",
                  "bg-gray-800/50 border border-purple-500/30 hover:border-purple-500/50",
                  isSelected && "bg-purple-900/30 border-purple-500"
                )}
              >
                <input
                  type={is_multiselect ? "checkbox" : "radio"}
                  name={id}
                  value={choice.value}
                  checked={isSelected}
                  onChange={() => handleChoiceChange(choice.value)}
                  className="w-4 h-4 text-purple-600 bg-gray-700 border-gray-600 rounded focus:ring-purple-500"
                />
                <span className="text-sm text-gray-300">{choice.title}</span>
              </label>
            );
          })}
        </motion.div>
      );

    default:
      return null;
  }
};
