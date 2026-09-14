-- Customer Service QA database initialization for MySQL 8.0+
-- Database/user credentials are provided by Docker Compose through .env.

CREATE DATABASE IF NOT EXISTS `{{MYSQL_DATABASE}}`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE `{{MYSQL_DATABASE}}`;

CREATE TABLE IF NOT EXISTS `cs_qa` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '数据库主键',
    `qa_code` VARCHAR(100) NOT NULL COMMENT '业务唯一编号',
    `product_code` VARCHAR(50) NULL COMMENT '商品编码，NULL 表示通用问题',
    `product_name` VARCHAR(200) NULL COMMENT '商品名称，便于导入校验和展示',

    `standard_question` VARCHAR(500) NOT NULL COMMENT '标准问题',
    `standard_answer` TEXT NOT NULL COMMENT '标准答案',
    `similar_questions` JSON NULL COMMENT '相似问法 JSON 字符串数组',
    `keywords` JSON NULL COMMENT '检索关键词 JSON 字符串数组',

    `intent_code` VARCHAR(100) NULL COMMENT '意图编码',
    `question_type` VARCHAR(50) NULL COMMENT '问题分类',
    `service_stage` VARCHAR(20) NOT NULL DEFAULT 'general' COMMENT 'pre_sale/post_sale/general',

    `required_points` JSON NULL COMMENT '必答要点 JSON 字符串数组',
    `prohibited_expressions` JSON NULL COMMENT '禁止表达 JSON 字符串数组',
    `risk_level` VARCHAR(20) NOT NULL DEFAULT 'low' COMMENT 'low/medium/high',
    `need_human` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否必须转人工',

    `applicable_version` VARCHAR(100) NULL COMMENT '适用产品资料版本',
    `answer_version` INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '答案版本号',
    `priority` INT NOT NULL DEFAULT 0 COMMENT '命中优先级，越大越优先',

    `status` VARCHAR(20) NOT NULL DEFAULT 'draft' COMMENT 'draft/published/archived',
    `review_status` VARCHAR(30) NOT NULL DEFAULT 'pending_validation' COMMENT 'usable/pending_review/pending_validation',
    `source` VARCHAR(255) NULL COMMENT '数据来源',
    `import_batch_no` VARCHAR(64) NULL COMMENT '导入批次号',
    `source_row_no` INT UNSIGNED NULL COMMENT '源文件行号',

    `effective_at` DATETIME NULL COMMENT '生效时间',
    `expired_at` DATETIME NULL COMMENT '失效时间',
    `created_by` VARCHAR(100) NULL COMMENT '创建人',
    `approved_by` VARCHAR(100) NULL COMMENT '审核人',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_cs_qa_code` (`qa_code`),
    KEY `idx_cs_qa_product` (`product_code`),
    KEY `idx_cs_qa_intent` (`intent_code`),
    KEY `idx_cs_qa_type_status` (`question_type`, `status`),
    KEY `idx_cs_qa_stage_status` (`service_stage`, `status`),
    KEY `idx_cs_qa_review_status` (`review_status`),
    KEY `idx_cs_qa_effective` (`effective_at`, `expired_at`),
    CONSTRAINT `ck_cs_qa_service_stage`
        CHECK (`service_stage` IN ('pre_sale', 'post_sale', 'general')),
    CONSTRAINT `ck_cs_qa_risk_level`
        CHECK (`risk_level` IN ('low', 'medium', 'high')),
    CONSTRAINT `ck_cs_qa_status`
        CHECK (`status` IN ('draft', 'published', 'archived')),
    CONSTRAINT `ck_cs_qa_review_status`
        CHECK (`review_status` IN ('usable', 'pending_review', 'pending_validation')),
    CONSTRAINT `ck_cs_qa_need_human`
        CHECK (`need_human` IN (0, 1)),
    CONSTRAINT `ck_cs_qa_answer_version`
        CHECK (`answer_version` >= 1),
    CONSTRAINT `ck_cs_qa_effective_period`
        CHECK (`expired_at` IS NULL OR `effective_at` IS NULL OR `expired_at` > `effective_at`)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci
  COMMENT='智能客服标准 QA 知识表';
