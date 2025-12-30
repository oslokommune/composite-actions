terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "prod/app-hello/terraform.tfstate"
    region = "eu-west-1"
  }
}
