import { reactive } from "vue";
import type { FormRules } from "element-plus";

/**
 * 售后记录表单校验
 *
 * 必填口径与桌面端 form.py `_required_map` 一致：
 * 类型 / 球房 / 地区 / 问题；桌号桌面端为选填，这里同样不拦。
 */
export const formRules = reactive(<FormRules>{
  problem: [
    { required: true, message: "问题描述为必填项", trigger: "blur" },
    { min: 2, max: 500, message: "长度在 2 到 500 个字符", trigger: "blur" }
  ],
  issue_type: [
    { required: true, message: "请选择问题类型", trigger: "change" }
  ],
  room_name: [
    { required: true, message: "球房为必填项", trigger: "blur" }
  ],
  region: [{ required: true, message: "请选择/输入地区", trigger: "change" }],
  resolved: [
    { required: true, message: "请选择是否解决", trigger: "change" }
  ]
});
