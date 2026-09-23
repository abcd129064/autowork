import { $t } from "@/plugins/i18n";
import { aftersale } from "@/router/enums";

export default {
  path: "/aftersale",
  redirect: "/aftersale/list",
  meta: {
    icon: "ri/customer-service-2-line",
    title: $t("menus.pureAftersale"),
    rank: aftersale
  },
  children: [
    {
      path: "/aftersale/list",
      name: "AftersaleList",
      component: () => import("@/views/aftersale/index.vue"),
      meta: {
        icon: "ri/file-list-3-line",
        title: $t("menus.pureAftersaleList")
      }
    },
    {
      path: "/aftersale/stats",
      name: "AftersaleStats",
      component: () => import("@/views/aftersale/stats/index.vue"),
      meta: {
        icon: "ri/bar-chart-2-line",
        title: $t("menus.pureAftersaleStats")
      }
    },
    {
      path: "/aftersale/rank",
      name: "AftersaleRank",
      component: () => import("@/views/aftersale/rank/index.vue"),
      meta: {
        icon: "ri/trophy-line",
        title: $t("menus.pureAftersaleRank")
      }
    }
  ]
} satisfies RouteConfigsTable;
