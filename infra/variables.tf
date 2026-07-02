variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "deal-hunter"
}

variable "instance_type" {
  type    = string
  default = "t3.micro"
}

variable "db_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "cache_node_type" {
  type    = string
  default = "cache.t3.micro"
}

variable "github_repo" {
  type    = string
  default = "djsurt/product-tracker"
}

variable "budget_alert_email" {
  type = string
}

variable "db_name" {
  type    = string
  default = "deals"
}

variable "db_username" {
  type    = string
  default = "deals"
}
