from PIL import Image

image_path = (
    "C:/Projetos/Mentor.ia/pci-harvester/temp/prova/10/9.png"  # Path to the input image
)
output_path = "C:/Projetos/Mentor.ia/pci-harvester/cropped_image2.png"  # Path to save the cropped image
crop_box = [585, 47, 698, 305] # Define the crop box [ymin, xmin, ymax, xmax] on 0-1000 scale


def crop_image(image_path, output_path, crop_box):
    """
    Crop an image using normalized coordinates.

    Args:
        image_path: Path to the input image
        output_path: Path to save the cropped image
        crop_box: List/tuple in format [ymin, xmin, ymax, xmax] with values from 0-1000
    """
    # Open the image to get dimensions
    with Image.open(image_path) as img:
        img_width, img_height = img.size

        # Unpack normalized crop box coordinates
        ymin, xmin, ymax, xmax = crop_box

        # Convert from 0-1000 scale to pixel coordinates
        left = int(xmin * img_width / 1000)
        upper = int(ymin * img_height / 1000)
        right = int(xmax * img_width / 1000)
        lower = int(ymax * img_height / 1000)

        # PIL crop format: (left, upper, right, lower)
        pil_crop_box = (left, upper, right, lower)

        # Crop the image
        cropped_img = img.crop(pil_crop_box)

        # Save the cropped image
        cropped_img.save(output_path)

        print(f"Image dimensions: {img_width}x{img_height}")
        print(f"Normalized crop box: {crop_box}")
        print(f"PIL crop box (pixels): {pil_crop_box}")


def main():
    crop_image(image_path, output_path, crop_box)
    print(f"Cropped image saved to {output_path}")


if __name__ == "__main__":
    main()
