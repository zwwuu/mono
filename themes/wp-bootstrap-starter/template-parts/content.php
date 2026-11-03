<?php
/**
 * Card layout for posts in list views (index, archive, search).
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;
?>
<article id="post-<?php the_ID(); ?>" <?php post_class( 'card mb-4' ); ?>>
	<?php if ( has_post_thumbnail() ) : ?>
		<a href="<?php the_permalink(); ?>" aria-hidden="true" tabindex="-1">
			<?php the_post_thumbnail( 'wbs-featured', array( 'class' => 'card-img-top' ) ); ?>
		</a>
	<?php endif; ?>

	<div class="card-body">
		<?php
		the_title(
			sprintf(
				'<h2 class="card-title h4"><a href="%s" class="text-decoration-none link-dark">',
				esc_url( get_permalink() )
			),
			'</a></h2>'
		);
		?>
		<p class="card-text text-muted small"><?php wbs_posted_on(); ?></p>
		<div class="card-text"><?php the_excerpt(); ?></div>
		<a href="<?php the_permalink(); ?>" class="btn btn-primary btn-sm">
			<?php esc_html_e( 'Read more', WBS_TEXTDOMAIN ); ?>
		</a>
	</div>
</article>
