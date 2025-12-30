terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "prod/dns/terraform.tfstate"
    region = "eu-west-1"
  }
}
