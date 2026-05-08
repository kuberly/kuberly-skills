// AWS service icon mapping — node_type → iconify icon name.
//
// Icons resolve through @iconify/react which lazy-loads SVG data from the
// public Iconify CDN on first use. Bundle impact is minimal — only the
// single React component, not the icon data.
//
// Icon collections used:
//   logos:                 — vendor brand icons (logos:aws, logos:aws-eks, …)
//   simple-icons:          — fallback for services not in `logos:`
//   ph:                    — Phosphor icons for generic/abstract resources
//
// When iconify can't resolve a name it renders nothing; the ArchGrid falls
// back to the 2-letter glyph so we never show a blank tile.

const ICON: Record<string, string> = {
  // ---- Compute ----
  aws_account:                     "logos:aws",
  aws_ec2:                         "logos:aws-ec2",
  aws_instance:                    "logos:aws-ec2",
  aws_eks:                         "logos:aws-eks",
  aws_eks_cluster:                 "logos:aws-eks",
  aws_eks_nodegroup:               "logos:aws-eks",
  aws_eks_node_group:              "logos:aws-eks",
  aws_eks_addon:                   "logos:aws-eks",
  aws_eks_access_entry:            "logos:aws-eks",
  aws_eks_access_policy_association: "logos:aws-eks",
  aws_eks_fargate_profile:         "logos:aws-fargate",
  aws_fargate_profile:             "logos:aws-fargate",
  aws_lambda:                      "logos:aws-lambda",
  aws_lambda_function:             "logos:aws-lambda",

  // ---- Storage ----
  aws_s3:                          "logos:aws-s3",
  aws_s3_bucket:                   "logos:aws-s3",
  aws_s3_bucket_versioning:        "logos:aws-s3",
  aws_s3_bucket_lifecycle_configuration: "logos:aws-s3",
  aws_s3_bucket_public_access_block: "logos:aws-s3",
  aws_s3_bucket_server_side_encryption_configuration: "logos:aws-s3",
  s3_bucket:                       "logos:aws-s3",
  aws_ebs:                         "ph:hard-drives-fill",
  aws_ebs_volume:                  "ph:hard-drives-fill",
  ebs_volume:                      "ph:hard-drives-fill",
  aws_ecr_repo:                    "simple-icons:amazonaws",
  aws_ecr_repository:              "simple-icons:amazonaws",
  aws_ecr_repository_policy:       "simple-icons:amazonaws",
  aws_ecr_lifecycle_policy:        "simple-icons:amazonaws",
  ecr_repository:                  "simple-icons:amazonaws",
  aws_dlm_lifecycle_policy:        "ph:clock-counter-clockwise",

  // ---- Database ----
  aws_rds_cluster:                 "logos:aws-rds",
  aws_rds_instance:                "logos:aws-rds",
  rds_cluster:                     "logos:aws-rds",
  rds_instance:                    "logos:aws-rds",
  aws_elasticache:                 "logos:aws-elasticache",
  aws_elasticache_cluster:         "logos:aws-elasticache",
  aws_elasticache_user:            "logos:aws-elasticache",
  aws_elasticache_subnet_group:    "logos:aws-elasticache",
  elasticache_cluster:             "logos:aws-elasticache",

  // ---- Network ----
  aws_vpc:                         "ph:network",
  vpc:                             "ph:network",
  aws_subnet:                      "ph:graph",
  subnet:                          "ph:graph",
  aws_rtb:                         "ph:tree-structure",
  aws_route_table:                 "ph:tree-structure",
  route_table:                     "ph:tree-structure",
  aws_route_table_association:     "ph:tree-structure",
  aws_route:                       "ph:arrows-out-cardinal",
  aws_default_route_table:         "ph:tree-structure",
  aws_default_network_acl:         "ph:shield-check",
  aws_default_security_group:      "ph:shield",
  aws_nat:                         "ph:gateway",
  aws_nat_gateway:                 "ph:gateway",
  nat_gateway:                     "ph:gateway",
  aws_igw:                         "ph:globe-hemisphere-east",
  aws_internet_gateway:            "ph:globe-hemisphere-east",
  internet_gateway:                "ph:globe-hemisphere-east",
  aws_egress_only_internet_gateway: "ph:arrow-circle-right",
  aws_eip:                         "ph:hash",
  eip:                             "ph:hash",
  aws_vpce:                        "ph:plug",
  aws_vpc_endpoint:                "ph:plug",
  vpc_endpoint:                    "ph:plug",
  aws_flow_log:                    "ph:wave-sine",
  aws_lb:                          "logos:aws-elb",
  aws_lb_listener:                 "logos:aws-elb",
  aws_lb_target_group:             "logos:aws-elb",
  aws_lb_target_group_attachment:  "logos:aws-elb",
  load_balancer:                   "logos:aws-elb",
  alb:                             "logos:aws-elb",
  nlb:                             "logos:aws-elb",
  elb:                             "logos:aws-elb",

  // ---- Security/IAM ----
  aws_sg:                          "ph:shield",
  aws_security_group:              "ph:shield",
  aws_security_group_rule:         "ph:shield-check",
  security_group:                  "ph:shield",
  aws_iam_role:                    "logos:aws-iam",
  iam_role:                        "logos:aws-iam",
  aws_iam_policy:                  "logos:aws-iam",
  iam_policy:                      "logos:aws-iam",
  aws_iam_role_policy:             "logos:aws-iam",
  aws_iam_role_policy_attachment:  "logos:aws-iam",
  aws_iam_instance_profile:        "logos:aws-iam",
  iam_instance_profile:            "logos:aws-iam",
  aws_iam_group:                   "logos:aws-iam",
  aws_iam_group_policy_attachment: "logos:aws-iam",
  aws_iam_user:                    "ph:user",
  aws_iam_user_group_membership:   "ph:users",
  aws_iam_user_login_profile:      "ph:user-circle",
  aws_iam_service_linked_role:     "logos:aws-iam",
  aws_iam_openid_connect_provider: "ph:key",
  iam_principal:                   "ph:user-circle",
  aws_acm:                         "ph:certificate",
  acm_certificate:                 "ph:certificate",
  aws_kms_key:                     "logos:aws-kms",
  aws_kms_alias:                   "logos:aws-kms",
  kms_key:                         "logos:aws-kms",
  kms_alias:                       "logos:aws-kms",
  aws_secretsmanager_secret:       "logos:aws-secrets-manager",
  aws_secretsmanager_secret_version: "logos:aws-secrets-manager",
  aws_secret:                      "logos:aws-secrets-manager",
  secretsmanager_secret:           "logos:aws-secrets-manager",

  // ---- Edge/CDN ----
  aws_cloudfront:                  "logos:aws-cloudfront",
  aws_cloudfront_distribution:     "logos:aws-cloudfront",
  cloudfront_distribution:         "logos:aws-cloudfront",
  aws_r53_zone:                    "logos:aws-route-53",
  aws_route53_zone:                "logos:aws-route-53",
  route53_zone:                    "logos:aws-route-53",

  // ---- Monitoring & Events ----
  aws_cw_log_group:                "logos:aws-cloudwatch",
  aws_cloudwatch_log_group:        "logos:aws-cloudwatch",
  cloudwatch_log_group:            "logos:aws-cloudwatch",
  aws_cloudwatch_event_rule:       "logos:aws-eventbridge",
  aws_cloudwatch_event_target:     "logos:aws-eventbridge",

  // ---- Messaging ----
  aws_sqs_queue:                   "logos:aws-sqs",
  aws_sqs_queue_policy:            "logos:aws-sqs",
  aws_sns_topic:                   "logos:aws-sns",
};

const FALLBACK = "logos:aws";

export function awsIconForType(nodeType: string): string {
  return ICON[nodeType] ?? FALLBACK;
}
