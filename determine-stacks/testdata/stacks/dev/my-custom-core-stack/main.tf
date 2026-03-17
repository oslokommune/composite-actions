terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "dev/my-custom-core-stack/terraform.tfstate"
    region = "eu-west-1"
  }
}
