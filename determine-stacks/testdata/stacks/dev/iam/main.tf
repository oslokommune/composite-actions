terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "dev/iam/terraform.tfstate"
    region = "eu-west-1"
  }
}
