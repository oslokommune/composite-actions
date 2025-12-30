terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "dev/app-custom/terraform.tfstate"
    region = "eu-west-1"
  }
}
