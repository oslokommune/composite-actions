terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "dev/networking/terraform.tfstate"
    region = "eu-west-1"
  }
}
