terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "prod/applications/app-deep/terraform.tfstate"
    region = "eu-west-1"
  }
}
