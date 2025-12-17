terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "prod/app-too-tikki/terraform.tfstate"
    region = "eu-west-1"
  }
}
