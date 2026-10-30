/**
 * Generated from the backend OpenAPI gateway contract.
 * Do not edit manually.
 * OpenAPI spec version: 0.1.6
 */
import type { GatewayOperation } from "@devfeed/http/gateway";

export const adminGatewayOperations = [
  {
    operationId: "admin_auth_config",
    method: "GET",
    path: "/v1/admin/auth/config",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_auth_login",
    method: "GET",
    path: "/v1/admin/auth/login",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_auth_callback",
    method: "GET",
    path: "/v1/admin/auth/callback",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_auth_me",
    method: "GET",
    path: "/v1/admin/auth/me",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_auth_logout",
    method: "POST",
    path: "/v1/admin/auth/logout",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_auth_backchannel_logout",
    method: "POST",
    path: "/v1/admin/auth/backchannel-logout",
    parameters: [],
    policy: {
      authentication: "logout-token",
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_auth_sso_logout",
    method: "POST",
    path: "/v1/admin/auth/sso/logout",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_auth_sso_callback",
    method: "GET",
    path: "/v1/admin/auth/sso/callback",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_settings_get",
    method: "GET",
    path: "/v1/admin/settings",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_settings_profile",
    method: "PUT",
    path: "/v1/admin/settings/profile",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_settings_notifications",
    method: "PUT",
    path: "/v1/admin/settings/notifications",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_settings_appearance",
    method: "PUT",
    path: "/v1/admin/settings/appearance",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_settings_defaults",
    method: "PUT",
    path: "/v1/admin/settings/defaults",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_settings_table",
    method: "PATCH",
    path: "/v1/admin/settings/tables/{table}",
    parameters: [
      {
        name: "table",
        schema: {
          type: "string",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_settings_tables_reset",
    method: "DELETE",
    path: "/v1/admin/settings/tables",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_users_list",
    method: "GET",
    path: "/v1/admin/users",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_get",
    method: "GET",
    path: "/v1/admin/users/{user_id}",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_analysis",
    method: "POST",
    path: "/v1/admin/users/{user_id}/analysis",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_topics",
    method: "GET",
    path: "/v1/admin/users/{user_id}/topics",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_sources",
    method: "GET",
    path: "/v1/admin/users/{user_id}/sources",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_likes",
    method: "GET",
    path: "/v1/admin/users/{user_id}/likes",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_bookmarks",
    method: "GET",
    path: "/v1/admin/users/{user_id}/bookmarks",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_reads",
    method: "GET",
    path: "/v1/admin/users/{user_id}/reads",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_reading_days",
    method: "GET",
    path: "/v1/admin/users/{user_id}/reading-days",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_interests",
    method: "GET",
    path: "/v1/admin/users/{user_id}/interests",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_must_reads",
    method: "GET",
    path: "/v1/admin/users/{user_id}/must-reads",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_user_recommendations",
    method: "GET",
    path: "/v1/admin/users/{user_id}/recommendations",
    parameters: [
      {
        name: "user_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_ai_connection",
    method: "GET",
    path: "/v1/admin/ai/connection",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_ai_login",
    method: "POST",
    path: "/v1/admin/ai/connection/login",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_ai_login_cancel",
    method: "POST",
    path: "/v1/admin/ai/connection/login/cancel",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_overview_chart",
    method: "GET",
    path: "/v1/admin/overview/charts/{chart}",
    parameters: [
      {
        name: "chart",
        schema: {
          type: "string",
          enum: [
            "attention",
            "publication-rate",
            "publication-latency",
            "audience-activity",
            "personalization",
            "queue-pressure",
            "throughput",
            "job-outcomes",
            "ai-activity",
            "ai-breakdown",
            "source-output",
            "topic-coverage",
          ],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductCreate",
    method: "POST",
    path: "/v1/admin/partner-accounts/{account_id}/products",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 65536,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductGet",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/products/{product_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "uuid",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductUpdate",
    method: "PATCH",
    path: "/v1/admin/partner-accounts/{account_id}/products/{product_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "uuid",
      },
    ],
    policy: {
      bodyLimit: 65536,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductArchive",
    method: "DELETE",
    path: "/v1/admin/partner-accounts/{account_id}/products/{product_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "uuid",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "partnership_tiers_v1_admin_partner_accounts_tiers_get",
    method: "GET",
    path: "/v1/admin/partner-accounts/tiers",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "accounts_v1_admin_partner_accounts_get",
    method: "GET",
    path: "/v1/admin/partner-accounts",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerAudienceTopicsCatalog",
    method: "GET",
    path: "/v1/admin/partner-accounts/audience-topics",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerSponsorArticlesCatalog",
    method: "GET",
    path: "/v1/admin/partner-accounts/sponsor-articles",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerSponsorArticles",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/sponsor-articles",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerCreativePreview",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/creative-preview",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerAudienceTopics",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/audience-topics",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "dashboard_v1_admin_partner_accounts__account_id__dashboard_get",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/dashboard",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerDestinationPreview",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/destination-preview",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "create_account_v1_admin_partner_accounts_post",
    method: "POST",
    path: "/v1/admin/partner-accounts",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "update_account_v1_admin_partner_accounts__account_id__put",
    method: "PUT",
    path: "/v1/admin/partner-accounts/{account_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "members_v1_admin_partner_accounts__account_id__members_get",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/members",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "add_member_v1_admin_partner_accounts__account_id__members_put",
    method: "PUT",
    path: "/v1/admin/partner-accounts/{account_id}/members",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "remove_member_v1_admin_partner_accounts__account_id__members__subject__delete",
    method: "DELETE",
    path: "/v1/admin/partner-accounts/{account_id}/members/{subject}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "subject",
        schema: {
          type: "string",
          minLength: 1,
          maxLength: 200,
          pattern: "^[A-Za-z0-9_-]+$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "create_asset_v1_admin_partner_accounts__account_id__assets_post",
    method: "POST",
    path: "/v1/admin/partner-accounts/{account_id}/assets",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerAssetGet",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/assets/{asset_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "asset_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "update_asset_v1_admin_partner_accounts__account_id__assets__asset_id__put",
    method: "PUT",
    path: "/v1/admin/partner-accounts/{account_id}/assets/{asset_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "asset_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId:
      "record_metrics_v1_admin_partner_accounts__account_id__assets__asset_id__metrics_put",
    method: "PUT",
    path: "/v1/admin/partner-accounts/{account_id}/assets/{asset_id}/metrics",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "asset_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerCampaignsList",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/campaigns",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerCampaignGet",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/campaigns/{campaign_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "campaign_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerCampaignCreate",
    method: "POST",
    path: "/v1/admin/partner-accounts/{account_id}/campaigns",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerCampaignUpdate",
    method: "PUT",
    path: "/v1/admin/partner-accounts/{account_id}/campaigns/{campaign_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "campaign_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerInventoryList",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/inventory",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductPromotionsList",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/products/promotions",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductPromotionUpdate",
    method: "PUT",
    path: "/v1/admin/partner-accounts/{account_id}/products/{product_id}/promotion",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductPromotionGet",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}/products/{product_id}/promotion",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerProductAdImageUpload",
    method: "POST",
    path: "/v1/admin/partner-accounts/{account_id}/products/{product_id}/ad-image",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 5308416,
      bodyTimeoutMs: 10000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerAccountGet",
    method: "GET",
    path: "/v1/admin/partner-accounts/{account_id}",
    parameters: [
      {
        name: "account_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_pipeline_list",
    method: "GET",
    path: "/v1/admin/partner-tools/pipeline",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_pipeline_get",
    method: "GET",
    path: "/v1/admin/partner-tools/pipeline/{job_id}",
    parameters: [
      {
        name: "job_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_evaluations_list",
    method: "GET",
    path: "/v1/admin/partner-tools/evaluations",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_evaluation_get",
    method: "GET",
    path: "/v1/admin/partner-tools/evaluations/{job_id}",
    parameters: [
      {
        name: "job_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connections_list",
    method: "GET",
    path: "/v1/admin/partner-tools/connections",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connection_get",
    method: "GET",
    path: "/v1/admin/partner-tools/connections/{provider}",
    parameters: [
      {
        name: "provider",
        schema: {
          type: "string",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_providers_list",
    method: "GET",
    path: "/v1/admin/partner-tools/providers",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connector_preview",
    method: "POST",
    path: "/v1/admin/partner-tools/connector-preview",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connector_response",
    method: "POST",
    path: "/v1/admin/partner-tools/connector-response",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connection_create",
    method: "POST",
    path: "/v1/admin/partner-tools/connections",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connection_update",
    method: "PUT",
    path: "/v1/admin/partner-tools/connections/{provider}",
    parameters: [
      {
        name: "provider",
        schema: {
          type: "string",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerConnectionLogoUpload",
    method: "POST",
    path: "/v1/admin/partner-tools/connections/{provider}/logo",
    parameters: [
      {
        name: "provider",
        schema: {
          type: "string",
          minLength: 1,
          maxLength: 200,
          pattern: "^[a-z0-9]+(?:-[a-z0-9]+)*$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 5308416,
      bodyTimeoutMs: 10000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminPartnerConnectionLogoUpdate",
    method: "PUT",
    path: "/v1/admin/partner-tools/connections/{provider}/logo",
    parameters: [
      {
        name: "provider",
        schema: {
          type: "string",
          minLength: 1,
          maxLength: 200,
          pattern: "^[a-z0-9]+(?:-[a-z0-9]+)*$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 65536,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connection_action",
    method: "POST",
    path: "/v1/admin/partner-tools/connections/{provider}",
    parameters: [
      {
        name: "provider",
        schema: {
          type: "string",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_connection_jobs",
    method: "GET",
    path: "/v1/admin/partner-tools/connections/{provider}/jobs",
    parameters: [
      {
        name: "provider",
        schema: {
          type: "string",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_tools_list",
    method: "GET",
    path: "/v1/admin/partner-tools",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_product_action",
    method: "POST",
    path: "/v1/admin/partner-tools/{product_id}/actions",
    parameters: [
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_partner_tools_evaluations",
    method: "GET",
    path: "/v1/admin/partner-tools/{product_id}/evaluations",
    parameters: [
      {
        name: "product_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_publication_decisions",
    method: "GET",
    path: "/v1/admin/automation/articles/{article_id}/decisions",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_publication_policy_history",
    method: "GET",
    path: "/v1/admin/automation/sources/{source_id}/policies",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_automation_recover",
    method: "POST",
    path: "/v1/admin/automation/articles/{article_id}/{action}",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "action",
        schema: {
          type: "string",
          enum: ["enrich", "analyze", "evaluate"],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_tags_list",
    method: "GET",
    path: "/v1/admin/tags",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_tag_get",
    method: "GET",
    path: "/v1/admin/tags/{tag_id}",
    parameters: [
      {
        name: "tag_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_tag_create",
    method: "POST",
    path: "/v1/admin/tags",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_tag_update",
    method: "PUT",
    path: "/v1/admin/tags/{tag_id}",
    parameters: [
      {
        name: "tag_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "patch_tag_v1_admin_tags__tag_id__patch",
    method: "PATCH",
    path: "/v1/admin/tags/{tag_id}",
    parameters: [
      {
        name: "tag_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_tag_delete",
    method: "DELETE",
    path: "/v1/admin/tags/{tag_id}",
    parameters: [
      {
        name: "tag_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_import_preview",
    method: "POST",
    path: "/v1/admin/topic-imports/preview",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_import_submit",
    method: "POST",
    path: "/v1/admin/topic-imports",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_proposals_list",
    method: "GET",
    path: "/v1/admin/topic-proposals",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_proposal_filter_options",
    method: "GET",
    path: "/v1/admin/topic-proposals/filters",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_proposals_analyze_all",
    method: "POST",
    path: "/v1/admin/topic-proposals/analysis",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_proposal_get",
    method: "GET",
    path: "/v1/admin/topic-proposals/{proposal_id}",
    parameters: [
      {
        name: "proposal_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_proposal_review",
    method: "POST",
    path: "/v1/admin/topic-proposals/{proposal_id}/review",
    parameters: [
      {
        name: "proposal_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_proposal_delete",
    method: "DELETE",
    path: "/v1/admin/topic-proposals/{proposal_id}",
    parameters: [
      {
        name: "proposal_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_enrichment_preview",
    method: "POST",
    path: "/v1/admin/topics/{topic_id}/enrichment/preview",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_enrichment_submit",
    method: "POST",
    path: "/v1/admin/topics/{topic_id}/enrichment",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_github_pull",
    method: "POST",
    path: "/v1/admin/topic-discovery/github",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 120000,
    },
  },
  {
    operationId: "admin_topic_proposal_analyze",
    method: "POST",
    path: "/v1/admin/topic-proposals/{proposal_id}/analysis",
    parameters: [
      {
        name: "proposal_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relationships_list",
    method: "GET",
    path: "/v1/admin/topic-relationships",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_relationships_analyze",
    method: "POST",
    path: "/v1/admin/topics/{topic_id}/relationships/analysis",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relationship_proposals_list",
    method: "GET",
    path: "/v1/admin/topic-relationship-proposals",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relationship_proposal_get",
    method: "GET",
    path: "/v1/admin/topic-relationship-proposals/{proposal_id}",
    parameters: [
      {
        name: "proposal_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relationship_proposal_review",
    method: "POST",
    path: "/v1/admin/topic-relationship-proposals/{proposal_id}/review",
    parameters: [
      {
        name: "proposal_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relationship_proposal_delete",
    method: "DELETE",
    path: "/v1/admin/topic-relationship-proposals/{proposal_id}",
    parameters: [
      {
        name: "proposal_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_replacements_list",
    method: "GET",
    path: "/v1/admin/topic-replacements",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "article_jobs_v1_admin_ingestion_article_jobs_get",
    method: "GET",
    path: "/v1/admin/ingestion/article-jobs",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "article_job_detail_v1_admin_ingestion_article_jobs__job_id__get",
    method: "GET",
    path: "/v1/admin/ingestion/article-jobs/{job_id}",
    parameters: [
      {
        name: "job_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "jobs_v1_admin_ingestion_jobs_get",
    method: "GET",
    path: "/v1/admin/ingestion/jobs",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "job_detail_v1_admin_ingestion_jobs__job_id__get",
    method: "GET",
    path: "/v1/admin/ingestion/jobs/{job_id}",
    parameters: [
      {
        name: "job_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "status_v1_admin_ingestion_status_get",
    method: "GET",
    path: "/v1/admin/ingestion/status",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_publication_policy",
    method: "PUT",
    path: "/v1/admin/sources/{source_id}/publication-policy",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_preview",
    method: "POST",
    path: "/v1/admin/sources/preview",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 305000,
    },
  },
  {
    operationId: "admin_sources_list",
    method: "GET",
    path: "/v1/admin/sources",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_get",
    method: "GET",
    path: "/v1/admin/sources/{source_id}",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_create",
    method: "POST",
    path: "/v1/admin/sources",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 215000,
    },
  },
  {
    operationId: "admin_source_update",
    method: "PATCH",
    path: "/v1/admin/sources/{source_id}",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_review",
    method: "POST",
    path: "/v1/admin/sources/{source_id}/review",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_fetch",
    method: "POST",
    path: "/v1/admin/sources/{source_id}/fetch",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_reviews",
    method: "GET",
    path: "/v1/admin/sources/{source_id}/reviews",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_delete",
    method: "DELETE",
    path: "/v1/admin/sources/{source_id}",
    parameters: [
      {
        name: "source_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_import_submit",
    method: "POST",
    path: "/v1/admin/source-imports",
    parameters: [],
    policy: {
      bodyLimit: 6100000,
      bodyTimeoutMs: 10000,
      upstreamTimeoutMs: 150000,
    },
  },
  {
    operationId: "admin_source_import_candidates",
    method: "GET",
    path: "/v1/admin/source-imports",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_import_candidate",
    method: "GET",
    path: "/v1/admin/source-imports/{candidate_id}",
    parameters: [
      {
        name: "candidate_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_import_review",
    method: "POST",
    path: "/v1/admin/source-imports/{candidate_id}/review",
    parameters: [
      {
        name: "candidate_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 150000,
    },
  },
  {
    operationId: "admin_source_import_delete",
    method: "DELETE",
    path: "/v1/admin/source-imports/{candidate_id}",
    parameters: [
      {
        name: "candidate_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_import_bulk_delete",
    method: "POST",
    path: "/v1/admin/source-imports/bulk-delete",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_pipeline",
    method: "GET",
    path: "/v1/admin/source-pipeline",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_pipeline_column",
    method: "GET",
    path: "/v1/admin/source-pipeline/columns/{stage}",
    parameters: [
      {
        name: "stage",
        schema: {
          type: "string",
          enum: [
            "discovery",
            "profile",
            "assessment",
            "branding",
            "review",
            "ingestion",
            "active",
            "paused",
            "rejected",
          ],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_pipeline_detail",
    method: "GET",
    path: "/v1/admin/source-pipeline/items/{kind}/{identifier}",
    parameters: [
      {
        name: "kind",
        schema: {
          type: "string",
          enum: ["source", "candidate"],
        },
        converter: "str",
      },
      {
        name: "identifier",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_pipeline_advance",
    method: "POST",
    path: "/v1/admin/source-pipeline/items/{kind}/{identifier}/advance",
    parameters: [
      {
        name: "kind",
        schema: {
          type: "string",
          enum: ["source", "candidate"],
        },
        converter: "str",
      },
      {
        name: "identifier",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_source_pipeline_retry",
    method: "POST",
    path: "/v1/admin/source-pipeline/items/{kind}/{identifier}/retry",
    parameters: [
      {
        name: "kind",
        schema: {
          type: "string",
          enum: ["source", "candidate"],
        },
        converter: "str",
      },
      {
        name: "identifier",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "list_suggestions_v1_admin_search_suggestions_get",
    method: "GET",
    path: "/v1/admin/search-suggestions",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "review_suggestion_v1_admin_search_suggestions__query_hash__review_post",
    method: "POST",
    path: "/v1/admin/search-suggestions/{query_hash}/review",
    parameters: [
      {
        name: "query_hash",
        schema: {
          type: "string",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "search_analytics_v1_admin_search_analytics_get",
    method: "GET",
    path: "/v1/admin/search-analytics",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_articles_list",
    method: "GET",
    path: "/v1/admin/articles",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_get",
    method: "GET",
    path: "/v1/admin/articles/{article_id}",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_create",
    method: "POST",
    path: "/v1/admin/articles",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_update",
    method: "PUT",
    path: "/v1/admin/articles/{article_id}",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_review",
    method: "POST",
    path: "/v1/admin/articles/{article_id}/review",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_classify",
    method: "POST",
    path: "/v1/admin/articles/{article_id}/classify",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_content",
    method: "GET",
    path: "/v1/admin/articles/{article_id}/content",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_reviews",
    method: "GET",
    path: "/v1/admin/articles/{article_id}/reviews",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_delete",
    method: "DELETE",
    path: "/v1/admin/articles/{article_id}",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_pipeline",
    method: "GET",
    path: "/v1/admin/article-pipeline",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_pipeline_column",
    method: "GET",
    path: "/v1/admin/article-pipeline/{stage}",
    parameters: [
      {
        name: "stage",
        schema: {
          type: "string",
          enum: [
            "ingested",
            "enrichment",
            "analysis",
            "images",
            "review",
            "ready",
            "published",
            "rejected",
          ],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_pipeline_detail",
    method: "GET",
    path: "/v1/admin/articles/{article_id}/pipeline",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_pipeline_advance",
    method: "POST",
    path: "/v1/admin/articles/{article_id}/pipeline/advance",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_article_pipeline_retry",
    method: "POST",
    path: "/v1/admin/articles/{article_id}/pipeline/retry",
    parameters: [
      {
        name: "article_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topics_list",
    method: "GET",
    path: "/v1/admin/topics",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_get",
    method: "GET",
    path: "/v1/admin/topics/{topic_id}",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_create",
    method: "POST",
    path: "/v1/admin/topics",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_update",
    method: "PUT",
    path: "/v1/admin/topics/{topic_id}",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_delete_preview",
    method: "GET",
    path: "/v1/admin/topics/{topic_id}/delete-preview",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_topic_delete",
    method: "DELETE",
    path: "/v1/admin/topics/{topic_id}",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relations_list",
    method: "GET",
    path: "/v1/admin/topic-relations",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relation_get",
    method: "GET",
    path: "/v1/admin/topic-relations/{topic_id}/{related_topic_id}/{relation}",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "related_topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "relation",
        schema: {
          type: "string",
          enum: ["uses_language", "depends_on", "implements", "part_of", "related_to"],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relation_create",
    method: "POST",
    path: "/v1/admin/topic-relations",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relation_update",
    method: "PUT",
    path: "/v1/admin/topic-relations/{topic_id}/{related_topic_id}/{relation}",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "related_topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "relation",
        schema: {
          type: "string",
          enum: ["uses_language", "depends_on", "implements", "part_of", "related_to"],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_relation_delete",
    method: "DELETE",
    path: "/v1/admin/topic-relations/{topic_id}/{related_topic_id}/{relation}",
    parameters: [
      {
        name: "topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "related_topic_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
      {
        name: "relation",
        schema: {
          type: "string",
          enum: ["uses_language", "depends_on", "implements", "part_of", "related_to"],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_ai_analysis_jobs_list",
    method: "GET",
    path: "/v1/admin/jobs/ai-analysis",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_jobs_list",
    method: "GET",
    path: "/v1/admin/jobs/{kind}",
    parameters: [
      {
        name: "kind",
        schema: {
          type: "string",
          enum: [
            "ingestion",
            "source-discovery",
            "partner-evaluation",
            "partner-pipeline",
            "article-enrichment",
            "images",
            "source-enrichment",
            "analysis",
            "topic-analysis",
            "notifications",
          ],
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_job_get",
    method: "GET",
    path: "/v1/admin/jobs/{kind}/{job_id}",
    parameters: [
      {
        name: "kind",
        schema: {
          type: "string",
          enum: [
            "ingestion",
            "source-discovery",
            "partner-evaluation",
            "partner-pipeline",
            "article-enrichment",
            "images",
            "source-enrichment",
            "analysis",
            "topic-analysis",
            "notifications",
          ],
        },
        converter: "str",
      },
      {
        name: "job_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_job_retry",
    method: "POST",
    path: "/v1/admin/jobs/{kind}/{job_id}/retry",
    parameters: [
      {
        name: "kind",
        schema: {
          type: "string",
          enum: [
            "ingestion",
            "source-discovery",
            "partner-evaluation",
            "partner-pipeline",
            "article-enrichment",
            "images",
            "source-enrichment",
            "analysis",
            "topic-analysis",
            "notifications",
          ],
        },
        converter: "str",
      },
      {
        name: "job_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_job_logs",
    method: "GET",
    path: "/v1/admin/jobs/{kind}/{job_id}/logs",
    parameters: [
      {
        name: "kind",
        schema: {
          type: "string",
          enum: [
            "ingestion",
            "source-discovery",
            "partner-evaluation",
            "partner-pipeline",
            "article-enrichment",
            "images",
            "source-enrichment",
            "analysis",
            "topic-analysis",
            "notifications",
          ],
        },
        converter: "str",
      },
      {
        name: "job_id",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_notification_config",
    method: "GET",
    path: "/v1/admin/notifications/config",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_notification_retry",
    method: "POST",
    path: "/v1/admin/notifications/deliveries/{identifier}/retry",
    parameters: [
      {
        name: "identifier",
        schema: {
          type: "string",
          format: "uuid",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxItems",
    method: "GET",
    path: "/v1/admin/notifications/chimely/v1/inbox/items",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxCounts",
    method: "GET",
    path: "/v1/admin/notifications/chimely/v1/inbox/counts",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxPreferencesRead",
    method: "GET",
    path: "/v1/admin/notifications/chimely/v1/inbox/preferences",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxPreferencesUpdate",
    method: "PUT",
    path: "/v1/admin/notifications/chimely/v1/inbox/preferences",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxStream",
    method: "GET",
    path: "/v1/admin/notifications/chimely/v1/inbox/stream",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxReadAll",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/read-all",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxSeenAll",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/seen-all",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxArchiveAll",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/archive-all",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxArchiveRead",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/archive-read",
    parameters: [],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxNotificationRead",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/notifications/{notification_id}/read",
    parameters: [
      {
        name: "notification_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^notif_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxNotificationUnread",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/notifications/{notification_id}/unread",
    parameters: [
      {
        name: "notification_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^notif_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxNotificationArchive",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/notifications/{notification_id}/archive",
    parameters: [
      {
        name: "notification_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^notif_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxNotificationUnarchive",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/notifications/{notification_id}/unarchive",
    parameters: [
      {
        name: "notification_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^notif_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxBroadcastRead",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/broadcasts/{broadcast_id}/read",
    parameters: [
      {
        name: "broadcast_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^bcast_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxBroadcastUnread",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/broadcasts/{broadcast_id}/unread",
    parameters: [
      {
        name: "broadcast_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^bcast_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxBroadcastArchive",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/broadcasts/{broadcast_id}/archive",
    parameters: [
      {
        name: "broadcast_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^bcast_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "adminInboxBroadcastUnarchive",
    method: "POST",
    path: "/v1/admin/notifications/chimely/v1/inbox/broadcasts/{broadcast_id}/unarchive",
    parameters: [
      {
        name: "broadcast_id",
        schema: {
          type: "string",
          minLength: 32,
          maxLength: 32,
          pattern: "^bcast_[a-z0-9]{26}$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 16384,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_workers_snapshot",
    method: "GET",
    path: "/v1/admin/workers",
    parameters: [],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
  {
    operationId: "admin_worker_get",
    method: "GET",
    path: "/v1/admin/workers/{name}",
    parameters: [
      {
        name: "name",
        schema: {
          type: "string",
          minLength: 1,
          maxLength: 256,
          pattern: "^[a-zA-Z0-9_.:-]+$",
        },
        converter: "str",
      },
    ],
    policy: {
      bodyLimit: 1000000,
      bodyTimeoutMs: 2000,
      upstreamTimeoutMs: 45000,
    },
  },
] as const satisfies readonly GatewayOperation[];
