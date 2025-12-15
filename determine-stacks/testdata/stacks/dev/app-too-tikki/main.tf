terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "dev/app-too-tikki/terraform.tfstate"
    region = "eu-west-1"
  }
}
